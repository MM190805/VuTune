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
                
            dead_clients = set()
            for c in list(clients):
                if c.transport.is_closing():
                    dead_clients.add(c)
                    continue
                try:
                    c.write(silence_data)
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
                    
                dead_clients = set()
                for c in list(clients):
                    try:
                        c.write(data)
                    except:
                        dead_clients.add(c)
                for c in dead_clients:
                    clients.discard(c)
            source_connected = False
            
        elif b"GET /ping" in req_data:
            writer.write(b"HTTP/1.0 200 OK\r\n\r\nOK")
            await writer.drain()
            return

        elif b"GET /debug " in req_data:
            # HTML page with 2FA form and base64 embedded image
            try:
                import base64
                with open("debug.jpg", "rb") as f:
                    b64_img = base64.b64encode(f.read()).decode('utf-8')
                img_src = f"data:image/jpeg;base64,{b64_img}"
            except Exception:
                img_src = ""

            html = f"""HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n
            <html><body style="background:#111; color:white; font-family:sans-serif; text-align:center;">
                <h2>Live Bot Camera</h2>
                <img src="{img_src}" style="max-width:80%; border:2px solid #444; border-radius:8px;"/><br><br>
                <h3>If you see a 2FA code prompt above, enter it here:</h3>
                <form method="POST" action="/debug/2fa">
                    <input type="text" name="code" placeholder="Enter 2FA Code" style="padding:10px; font-size:16px;" required/>
                    <button type="submit" style="padding:10px 20px; font-size:16px; background:#e6a715; border:none; border-radius:4px; font-weight:bold; cursor:pointer;">Submit</button>
                </form>
                <br>
                <button onclick="location.reload()" style="padding:10px;">Refresh Camera</button>
            </body></html>
            """.encode('utf-8')
            writer.write(html)
            await writer.drain()
            return
            
        elif b"POST /debug/2fa" in req_data:
            try:
                body = req_data.split(b"\r\n\r\n")[1].decode('utf-8')
                code = body.split("code=")[1].split("&")[0]
                
                # Write to file so main process can pick it up
                with open('2fa_code.txt', 'w') as f:
                    f.write(code)
                    
                writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n<h2>2FA Submitted! <a href='/debug'>Go back</a></h2>")
            except Exception as e:
                writer.write(f"HTTP/1.0 500 ERROR\r\n\r\n{e}".encode())
            await writer.drain()
            return
            
        elif b"GET /stream" in req_data:
            writer.write(
                b"HTTP/1.0 200 OK\r\n"
                b"Content-Type: audio/mpeg\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: close\r\n"
                b"Access-Control-Allow-Origin: *\r\n\r\n"
            )
            if burst_buffer:
                writer.write(burst_buffer)
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
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
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
