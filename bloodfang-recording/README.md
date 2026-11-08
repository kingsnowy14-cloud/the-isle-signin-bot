# BLOODFANG RECORDING

Windows desktop screen recorder with a branded watermark and instant replay.

## Features
- English desktop interface
- Capture a screen or individual window
- Optional microphone and system audio (system audio support depends on the Windows/Electron capture source)
- Instant Replay buffer: 15, 30, 60 or 120 seconds
- Save full recordings or replays locally as WebM
- Burned-in BLOODFANG RECORDING watermark
- No video uploads by default; captures stay on the user's PC

## Run locally
Requires Node.js 20+.

    cd bloodfang-recording
    npm install
    npm start

## Build Windows installer

    npm run dist

The installer is generated in dist/.

## Optional Railway API
The optional FastAPI service is separate from the recorder. Configure the Railway service root directory as /bloodfang-recording/railway. Install requirements.txt and start with:

    uvicorn server:app --host 0.0.0.0 --port $PORT

The API provides / and /health endpoints. It does not perform desktop capture; Electron records locally.

## Current MVP limitations
- Exports WebM; MP4 conversion can be added using FFmpeg.
- Instant replay is saved from recent MediaRecorder chunks and may begin up to about one chunk interval before the selected buffer.
- Global hotkeys, automatic updates, configurable watermark placement and an installer icon are not included yet.
- System audio availability depends on the selected capture source and OS permissions.