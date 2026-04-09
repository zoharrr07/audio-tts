import io
import os
import shutil
import tempfile
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response

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
