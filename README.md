# Audio TTS Project 🎙️

This project is a Vietnamese Text-to-Speech (TTS) system powered by the **Vieneu** engine. The core focus of the project is an automated **Worker** system that mass-generates audio based on database records, alongside a set of standalone APIs for testing purposes.

## 🌟 Key Features

### 1. Automation Worker (Core Feature)
An asynchronous background worker that runs continuously within the FastAPI application, responsible for:
- **Database Synchronization:** Periodically scans the **Supabase** Database (`audio_tasks` table) every 15 seconds to find text entries missing audio files (`status = pending`).
- **Cloud Storage Auto-Upload:** Processes text to speech and applies effects (voice cloning, pitch shift, etc.). Instead of storing files locally, the system integrates an automated upload flow to **Cloudflare R2** via the S3 protocol (`boto3`) and saves the public URL back to the database.
- **Auto-Retry Mechanism:** Equipped with robust error recovery; if the rendering model encounters an algorithm issue or memory limit, the system will automatically retry up to 3 times before marking the task as `failed`.

### 2. FastAPI Endpoints (Test Feature)
Although the Worker is the main component, the system exposes direct APIs (accessible via the FastAPI `/docs` UI) allowing users to test the Model interactively and download results immediately without going through the Database:
- `GET /tts`: Synthesize text using preset voices or adjust pitch/effects.
- `POST /clone-voice`: Perform zero-shot voice cloning using a short 3-10 second reference audio sample.
- _These endpoints return the raw `.wav` binary file directly to the browser._

## 🚀 Setup & Run

### Environment Variables
Create a `.env` file in the root directory (refer to `.env.example`) and fill in the parameters:
```env
# Supabase SDK Connection
SUPABASE_URL="https://xxx.supabase.co"
SUPABASE_KEY="your-anon-or-service-role-key"

# Cloudflare R2 / S3 Config (Unlocks Cloud Upload)
R2_ACCOUNT_ID="your_cloudflare_account_id"
R2_ACCESS_KEY_ID="your_access_key"
R2_SECRET_ACCESS_KEY="your_secret_key"
R2_BUCKET_NAME="audios"
R2_PUBLIC_URL_PREFIX="https://pub-xxxxxx.r2.dev"
```

### Running with Python Venv
Ensure you have `python 3.11+` installed.

```bash
# 1. Activate virtual environment
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the Server
./start.command
```

### Deploying with Docker (Production Ready)
The system includes a standard `Dockerfile` bundled with necessary OS-level libraries (libsndfile/gcc) required for Machine Learning workloads.
```bash
docker build -t audio-tts-worker .
docker run -p 8000:8000 --env-file .env audio-tts-worker
```

## 🛠 Required Supabase Schema
To ensure the application logic runs smoothly, make sure your Supabase project contains the following table structure:
- **Table Name:** `audio_tasks`
- **Required Columns:** 
  - `id` (Primary key - int)
  - `text` (string)
  - `voice` (string - default "0")
  - `status` (string - "pending", "processing", "completed", "failed")
  - `audio_path` (string - stores the Public URL)
  - `retry_count` (int - default 0) - *Crucial for the Auto-Retry mechanism to work.*
