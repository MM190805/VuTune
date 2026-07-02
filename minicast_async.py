import asyncio

clients = set()
burst_buffer = bytearray()
source_connected = False

with open('silence.mp3', 'rb') as f:
    silence_data = f.read()

async def fallback_streamer():
    global burst_buffer
    while True:
        if not source_connected:
            burst_buffer.extend(silence_data)
            if len(burst_buffer) > 256 * 1024:
                burst_buffer = bytearray(burst_buffer[-256 * 1024:])
                
            chunk_header = f"{len(silence_data):x}\r\n".encode()
            chunk_payload = chunk_header + silence_data + b"\r\n"
            
            dead_clients = set()
            for c in list(clients):
                if c.transport.is_closing():
                    dead_clients.add(c)
                    continue
                try:
                    c.write(chunk_payload)
                except:
                    dead_clients.add(c)
            
            for c in dead_clients:
                clients.discard(c)
                try:
                    c.close()
                except:
                    pass
        await asyncio.sleep(1)

async def handle_client(reader, writer):
    global burst_buffer, source_connected
    req_data = b""
    try:
        while b"\r\n\r\n" not in req_data:
            chunk = await reader.read(4096)
            if not chunk: break
            req_data += chunk
            
        if b"PUT /stream" in req_data or b"SOURCE /stream" in req_data:
            source_connected = True
            writer.write(b"HTTP/1.0 200 OK\r\n\r\n")
            await writer.drain()
            while True:
                data = await reader.read(8192)
                if not data: break
                burst_buffer.extend(data)
                if len(burst_buffer) > 256 * 1024:
                    burst_buffer = bytearray(burst_buffer[-256 * 1024:])
                    
                chunk_header = f"{len(data):x}\r\n".encode()
                chunk_payload = chunk_header + data + b"\r\n"
                
                dead_clients = set()
                for c in list(clients):
                    try:
                        c.write(chunk_payload)
                    except:
                        dead_clients.add(c)
                for c in dead_clients:
                    clients.discard(c)
            source_connected = False
            
        elif b"GET /ping" in req_data:
            writer.write(b"HTTP/1.0 200 OK\r\n\r\nOK")
            await writer.drain()
            
        elif b"GET /debug/screenshot" in req_data:
            try:
                import os
                if os.path.exists("debug.png"):
                    with open("debug.png", "rb") as f:
                        data = f.read()
                    writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: image/png\r\n\r\n" + data)
                else:
                    writer.write(b"HTTP/1.0 404 Not Found\r\n\r\nNo screenshot available.")
            except Exception as e:
                writer.write(b"HTTP/1.0 500 Internal Server Error\r\n\r\n" + str(e).encode())
            await writer.drain()
            
        elif b"GET /stream" in req_data:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: audio/mpeg\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Connection: keep-alive\r\n\r\n"
            )
            if burst_buffer:
                chunk_header = f"{len(burst_buffer):x}\r\n".encode()
                writer.write(chunk_header + burst_buffer + b"\r\n")
            await writer.drain()
            clients.add(writer)
            while True:
                await asyncio.sleep(1)
        else:
            writer.write(b"HTTP/1.0 404 Not Found\r\n\r\n")
            await writer.drain()
    except Exception as e:
        pass
    finally:
        clients.discard(writer)
        if b"PUT" in req_data or b"SOURCE" in req_data:
            source_connected = False

async def main():
    import os
    port = int(os.environ.get('PORT', 8000))
    server = await asyncio.start_server(handle_client, '0.0.0.0', port)
    print(f"Server listening on port {port}...")
    asyncio.create_task(fallback_streamer())
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())
