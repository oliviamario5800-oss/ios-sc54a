import socket, sys, time

PORT = 5140
print(f"Listening for live UDP kernel log on 0.0.0.0:{PORT}...")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", PORT))
except Exception as e:
    print(f"Failed to bind UDP port {PORT}: {e}")
    sys.exit(1)

log_file = r"C:\Users\user\Projects\ios-sc54a\live_log.txt"
with open(log_file, "a", encoding="utf-8", errors="replace") as f:
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            text = data.decode("utf-8", errors="replace")
            print(text, end="", flush=True)
            f.write(text)
            f.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error receiving: {e}")
            time.sleep(0.5)
