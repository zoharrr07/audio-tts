import io
import os
import shutil
import tempfile
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response
import asyncio
import uuid
import numpy as np
import soundfile as sf
from dotenv import load_dotenv
from supabase import create_client, Client
import boto3
from botocore.config import Config

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "audios")
R2_PUBLIC_URL_PREFIX = os.getenv("R2_PUBLIC_URL_PREFIX")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    
# Cấu hình R2 Client (Frame sườn - sẽ kích hoạt tự động khi bạn điền ENV)
s3_client = None
if R2_ACCOUNT_ID and R2_ACCESS_KEY_ID:
    s3_client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


# Ensure outputs directory exists
os.makedirs("outputs", exist_ok=True)

try:
    from vieneu import Vieneu
    VIENEU_AVAILABLE = True
except ImportError as e:
    VIENEU_AVAILABLE = False
    print(f"Vieneu error: {e}")

try:
    from pedalboard import Pedalboard, PitchShift, Distortion, HighpassFilter, LowpassFilter, Bitcrush, Chorus
except ImportError:
    pass

def apply_audio_effects(audio_array, sample_rate, pitch_shift: int, effect_preset: str):
    effects = []
    
    # 1. Pitch shift
    if pitch_shift != 0:
        effects.append(PitchShift(semitones=pitch_shift))
        
    # 2. Preset effects
    if effect_preset == "robotic":
        effects.append(Bitcrush(bit_depth=8))
        effects.append(Chorus())
    elif effect_preset == "telephone":
        effects.append(HighpassFilter(cutoff_frequency_hz=400))
        effects.append(LowpassFilter(cutoff_frequency_hz=3400))
    elif effect_preset == "monster":
        effects.append(Distortion(drive_db=10))
        if pitch_shift == 0:  # Auto lower pitch if not specified
            effects.append(PitchShift(semitones=-5))
            
    if not effects:
        return audio_array
        
    board = Pedalboard(effects)
    processed = board(audio_array, sample_rate)
    
    # Pedalboard returns shape (channels, frames). 
    # Soundfile expects (frames, channels) or 1D (frames,).
    if processed.ndim == 2:
        if processed.shape[0] == 1:
            processed = processed[0]  # Squeeze to 1D
        else:
            processed = processed.T   # Transpose to (frames, channels)
            
    return processed

app = FastAPI(title="Vietnamese Vieneu TTS API")

vieneu_tts = None

@app.on_event("startup")
def startup_event():
    if not supabase:
        print("Warning: Supabase credentials not found. Worker will fail if it tries to sync.")
    
    if VIENEU_AVAILABLE:
        try:
            global vieneu_tts
            print("Loading Vieneu-TTS model...")
            vieneu_tts = Vieneu()
            print("Loaded model: vieneu")
        except Exception as e:
            print(f"Error loading Vieneu-TTS: {e}")
    else:
        print("Vieneu-TTS not installed.")
        
    # Start the worker loop in the background
    asyncio.create_task(audio_worker_loop())

async def audio_worker_loop():
    print("Worker loop started. Waiting for tasks...")
    while True:
        try:
            await process_pending_task()
        except Exception as e:
            print(f"Worker Error: {e}")
        await asyncio.sleep(15)  # Check every 15 seconds

async def process_pending_task():
    if not supabase:
        return
        
    try:
        # Lấy một task đang chờ
        response = supabase.table("audio_tasks").select("*").eq("status", "pending").limit(1).execute()
        if not response.data or len(response.data) == 0:
            return

        task = response.data[0]
        task_id = task['id']
        retry_count = task.get('retry_count', 0)
        
        print(f"Worker: Found pending task {task_id} (Retry: {retry_count})")
        
        # Cập nhật trạng thái
        supabase.table("audio_tasks").update({"status": "processing"}).eq("id", task_id).execute()

        if not vieneu_tts:
            supabase.table("audio_tasks").update({
                "status": "failed",
                "error_message": "Vieneu TTS model is not loaded."
            }).eq("id", task_id).execute()
            return
            
        loop = asyncio.get_event_loop()
        
        def run_tts(params):
            voice_param = params['voice_param']
            try:
                voice_feature = vieneu_tts.get_preset_voice(voice_param)
            except ValueError:
                if os.path.exists(voice_param):
                    voice_feature = vieneu_tts.encode_reference(voice_param)
                else:
                    raise ValueError(f"Voice '{voice_param}' not found and is not a valid file.")
            
            audio_arr = vieneu_tts.infer(
                text=params['text'], 
                voice=voice_feature,
                temperature=params['temperature'],
                top_k=params['top_k'],
                max_chars=params['max_chars']
            )
            
            if params['pitch_shift'] != 0 or params['effect_preset']:
                audio_arr = apply_audio_effects(audio_arr, vieneu_tts.sample_rate, params['pitch_shift'], params['effect_preset'])
                
            max_val = np.max(np.abs(audio_arr))
            if max_val > 1.0:
                audio_arr = audio_arr / max_val
                
            return audio_arr
            
        # Map voice param
        raw_voice = str(task.get("voice", "0"))
        voice_param = raw_voice
        if raw_voice == "0":
            voice_param = "Bích Ngọc (Nữ - Miền Bắc)"
        elif raw_voice == "1":
            voice_param = "Phạm Tuyên (Nam - Miền Bắc)"
        elif raw_voice == "2":
            voice_param = "Thục Đoan (Nữ - Miền Nam)"
        elif raw_voice == "3":
            voice_param = "Xuân Vĩnh (Nam - Miền Nam)"
            
        audio_array = await loop.run_in_executor(None, run_tts, {
            'text': task.get('text', ''),
            'voice_param': voice_param,
            'temperature': task.get('temperature', 0.4),
            'top_k': task.get('top_k', 50),
            'max_chars': task.get('max_chars', 256),
            'pitch_shift': task.get('pitch_shift', 0),
            'effect_preset': task.get('effect_preset')
        })
        
        # Save file
        filename = f"task_{task_id}_{uuid.uuid4().hex[:8]}.wav"
        filepath = os.path.join("outputs", filename)
        
        sf.write(filepath, audio_array, vieneu_tts.sample_rate, format='WAV', subtype='PCM_16')
        
        # Khung sườn R2: Khởi tạo R2 URL
        audio_url = filepath
        
        # Nếu có thiết lập kết nối S3 Cloudflare R2
        # Tạm thời để dưới dạng config chờ sẵn. Hệ thống tự nhận biết khi ENV được kích hoạt
        if s3_client:
            try:
                s3_client.upload_file(filepath, R2_BUCKET_NAME, filename)
                # Ghép file path public URL
                if R2_PUBLIC_URL_PREFIX:
                    audio_url = f"{R2_PUBLIC_URL_PREFIX}/{filename}"
                    
                # Xóa file cục bộ để tiết kiệm bộ nhớ khi đã tải lên Cloud
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as s3_err:
                print(f"S3 Upload failed: {s3_err}")
                # Vẫn giữ nguyên audio_url dạng local nếu lỗi

        # Cập nhật thành công
        supabase.table("audio_tasks").update({
            "status": "completed",
            "audio_path": audio_url
        }).eq("id", task_id).execute()
        
        print(f"Worker: Task {task_id} completed. Saved to {audio_url}")
        
    except Exception as e:
        if 'task_id' in locals():
            try:
                # Quản lý retry count thay vì đánh tạch (failed) luôn
                if retry_count < 3:
                    supabase.table("audio_tasks").update({
                        "status": "pending",
                        "retry_count": retry_count + 1,
                        "error_message": f"Lỗi (Chờ Retry {retry_count + 1}/3): {str(e)}"
                    }).eq("id", task_id).execute()
                    print(f"Worker: Task {task_id} bị lỗi, sẽ thử lại. Error: {e}")
                else:
                    supabase.table("audio_tasks").update({
                        "status": "failed",
                        "error_message": f"Lỗi nghiêm trọng (Sụp đổ sau 3 lần thử): {str(e)}"
                    }).eq("id", task_id).execute()
                    print(f"Worker: Task {task_id} thất bại hoàn toàn.")
            except Exception as inner_e:
                print(f"Worker: Lỗi quá trình update DB Error state: {inner_e}")
        else:
            print(f"Worker: Lỗi chung: {e}")

@app.get("/tts")
def generate_tts(
    text: str = Query(..., description="Text to synthesize"),
    voice: str = Query("0", description="Speaker ID: '0', '1', '2', '3' or a file path"),
    temperature: float = Query(0.4, description="Expressiveness. Typical range 0.1 - 0.8"),
    top_k: int = Query(50, description="Highest token predictions. Typical range 10-100"),
    max_chars: int = Query(256, description="Chunk split size."),
    pitch_shift: int = Query(0, description="Pitch shift in semitones (e.g. 5 for chipmunk, -5 for deep)."),
    effect_preset: str = Query(None, description="Preset effect: 'robotic', 'telephone', 'monster'")
):
    """
    Generate speech from text utilizing Vieneu LLM-TTS.
    """
    if not vieneu_tts:
        raise HTTPException(status_code=500, detail="Vieneu model is not loaded.")
        
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    try:
        # Map numerical IDs to preset voices, or pass through as reference audio path
        voice_param = voice
        if voice == "0":
            voice_param = "Bích Ngọc (Nữ - Miền Bắc)"
        elif voice == "1":
            voice_param = "Phạm Tuyên (Nam - Miền Bắc)"
        elif voice == "2":
            voice_param = "Thục Đoan (Nữ - Miền Nam)"
        elif voice == "3":
            voice_param = "Xuân Vĩnh (Nam - Miền Nam)"

        try:
            # Fetch dictionary with embeddings for preset voices
            voice_feature = vieneu_tts.get_preset_voice(voice_param)
        except ValueError:
            # Otherwise assume it's a file path for voice cloning
            if os.path.exists(voice_param):
                voice_feature = vieneu_tts.encode_reference(voice_param)
            else:
                raise HTTPException(status_code=400, detail=f"Voice '{voice_param}' not found and is not a valid file.")
            
        audio_array = vieneu_tts.infer(
            text=text, 
            voice=voice_feature,
            temperature=temperature,
            top_k=top_k,
            max_chars=max_chars
        )
        import soundfile as sf
        import numpy as np
        
        # Apply voice effects if requested
        if pitch_shift != 0 or effect_preset:
            audio_array = apply_audio_effects(audio_array, vieneu_tts.sample_rate, pitch_shift, effect_preset)

        # Normalize ONLY if values exceed [-1.0, 1.0] to prevent clipping.
        # Using max_val > 1.0 ensures we don't accidentally amplify quiet audio
        # into loud static ("wind noise").
        max_val = np.max(np.abs(audio_array))
        if max_val > 1.0:
            audio_array = audio_array / max_val

        buffer = io.BytesIO()
        # Force PCM_16 subtype so browsers don't play loud static
        sf.write(buffer, audio_array, vieneu_tts.sample_rate, format='WAV', subtype='PCM_16')
        
        headers = {"Content-Disposition": "inline; filename=\"generated_audio.wav\""}
        return Response(content=buffer.getvalue(), media_type="audio/wav", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vieneu TTS Generation failed: {str(e)}")


@app.post("/clone-voice")
async def clone_voice(
    text: str = Form(..., description="Text to synthesize"),
    reference_audio: UploadFile = File(..., description="Reference audio file (3-10s)"),
    temperature: float = Form(0.4, description="Expressiveness. Typical range 0.1 - 0.8"),
    top_k: int = Form(50, description="Highest token predictions. Typical range 10-100"),
    max_chars: int = Form(256, description="Chunk split size."),
    pitch_shift: int = Form(0, description="Pitch shift in semitones (e.g. 5 for chipmunk, -5 for deep)."),
    effect_preset: str = Form(None, description="Preset effect: 'robotic', 'telephone', 'monster'")
):
    """
    Zero-Shot Voice Cloning using Vieneu.
    Submit Vietnamese text and a small reference audio (3-10 seconds of clear speech).
    """
    if not vieneu_tts:
        raise HTTPException(status_code=501, detail="Vieneu model is not loaded.")
        
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
        
    try:
        # Create temp file for input audio
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_in:
            # Copy uploaded audio to temp_in
            shutil.copyfileobj(reference_audio.file, temp_in)
            temp_in_path = temp_in.name
            
        # Run Voice Cloning
        # 1. Encode reference audio
        voice_feature = vieneu_tts.encode_reference(temp_in_path)
        
        # 2. Synthesize audio
        audio_array = vieneu_tts.infer(
            text=text, 
            voice=voice_feature,
            temperature=temperature,
            top_k=top_k,
            max_chars=max_chars
        )
        
        import soundfile as sf
        import numpy as np
        
        # Apply voice effects if requested
        if pitch_shift != 0 or effect_preset:
            audio_array = apply_audio_effects(audio_array, vieneu_tts.sample_rate, pitch_shift, effect_preset)

        # Prevent clipping
        max_val = np.max(np.abs(audio_array))
        if max_val > 1.0:
            audio_array = audio_array / max_val

        buffer = io.BytesIO()
        sf.write(buffer, audio_array, vieneu_tts.sample_rate, format='WAV', subtype='PCM_16')
        
        headers = {"Content-Disposition": "inline; filename=\"cloned_audio.wav\""}
        return Response(content=buffer.getvalue(), media_type="audio/wav", headers=headers)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Voice Cloning failed: {str(e)}")
    finally:
        # Clean up temporary file
        if 'temp_in_path' in locals() and os.path.exists(temp_in_path):
            os.remove(temp_in_path)

@app.get("/models")
def list_models():
    """List available loaded presets"""
    if not vieneu_tts:
        return {"loaded_models": {}}
    
    presets = []
    try:
        preset_list = vieneu_tts.list_preset_voices()
        for desc, id in preset_list:
            presets.append({"id": id, "description": desc})
    except Exception:
        pass
        
    return {
        "engine": "Vieneu TTS",
        "sample_rate": vieneu_tts.sample_rate if vieneu_tts else 24000,
        "presets": presets
    }

@app.get("/")
def root():
    return {
        "message": "Welcome to Vietnamese Vieneu TTS API.",
        "endpoints": ["GET /tts", "POST /clone-voice", "GET /models", "GET /docs"],
        "usage": "GET /tts?text=chào&voice=0&temperature=0.4"
    }
