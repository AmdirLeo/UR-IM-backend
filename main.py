from fastapi import FastAPI, WebSocket

app = FastAPI(title="IM System API")

@app.get("/")
async def root():
    return {"message": "Backend System Initialized"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("WebSocket connection established")
    await websocket.close()