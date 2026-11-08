from fastapi import FastAPI
app = FastAPI(title="BLOODFANG RECORDING API", version="1.0.0")
@app.get("/")
def root():
    return {"name": "BLOODFANG RECORDING", "status": "online"}
@app.get("/health")
def health():
    return {"status": "healthy"}