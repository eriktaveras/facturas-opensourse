#!/usr/bin/env python3
"""
Script de prueba para verificar la conexión WebSocket
"""
import asyncio
import websockets
import json

async def test_websocket():
    uri = "ws://localhost:8000/ws"

    try:
        print(f"🔌 Conectando a {uri}...")
        async with websockets.connect(uri) as websocket:
            print("✅ Conexión WebSocket establecida")

            # Esperar mensajes del servidor
            print("📡 Esperando mensajes del servidor...")
            for i in range(10):  # Esperar 10 mensajes o 60 segundos
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=6.0)
                    data = json.loads(message)
                    print(f"📨 Mensaje recibido: {data.get('type', 'unknown')} - {data.get('message', '')}")
                except asyncio.TimeoutError:
                    print("⏱️  Timeout - No se recibieron mensajes en 6 segundos")
                    # Enviar ping para mantener viva la conexión
                    await websocket.send("ping")
                    print("📤 Ping enviado")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())
