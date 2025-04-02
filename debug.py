import socket
import json
import asyncio
import sys

class AbletonConnectionDiagnostics:
    def __init__(self, host='127.0.0.1', port=65432):
        self.host = host
        self.port = port

    def check_socket_availability(self):
        """Check if the socket port is available"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                result = s.connect_ex((self.host, self.port))
                if result == 0:
                    print(f"✓ Port {self.port} is open and listening")
                    return True
                else:
                    print(f"✗ Port {self.port} is not available")
                    return False
        except Exception as e:
            print(f"✗ Socket check error: {e}")
            return False

    async def send_test_message(self):
        """Send a test JSON-RPC message to the daemon"""
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
            
            # Prepare test message
            test_message = {
                "jsonrpc": "2.0",
                "id": "connection_test",
                "method": "send_message",
                "params": {
                    "address": "/live/test",
                    "args": ["Connection Test"]
                }
            }
            
            # Send message
            message_json = json.dumps(test_message)
            print(f"[SENDING] Test message:\n{message_json}")
            
            writer.write(message_json.encode())
            await writer.drain()
            
            # Read response
            response = await reader.read(4096)
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            # Parse response
            try:
                response_json = json.loads(response.decode())
                print("[RECEIVED] Response:")
                print(json.dumps(response_json, indent=2))
                return response_json
            except json.JSONDecodeError as e:
                print(f"✗ JSON Decode Error: {e}")
                print(f"Raw response: {response.decode()}")
                return None
        
        except Exception as e:
            print(f"✗ Connection Error: {type(e).__name__} - {e}")
            return None

    async def retrieve_track_names(self):
        """Attempt to retrieve track names"""
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
            
            # Prepare track names request
            track_names_message = {
                "jsonrpc": "2.0",
                "id": "track_names_request",
                "method": "send_message",
                "params": {
                    "address": "/live/song/get/track_names",
                    "args": []
                }
            }
            
            # Send message
            message_json = json.dumps(track_names_message)
            print(f"[SENDING] Track Names Request:\n{message_json}")
            
            writer.write(message_json.encode())
            await writer.drain()
            
            # Read response
            response = await reader.read(4096)
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            # Parse response
            try:
                response_json = json.loads(response.decode())
                print("[RECEIVED] Track Names Response:")
                print(json.dumps(response_json, indent=2))
                return response_json
            except json.JSONDecodeError as e:
                print(f"✗ JSON Decode Error: {e}")
                print(f"Raw response: {response.decode()}")
                return None
        
        except Exception as e:
            print(f"✗ Track Names Retrieval Error: {type(e).__name__} - {e}")
            return None

async def main():
    diagnostics = AbletonConnectionDiagnostics()
    
    print("\n--- Socket Availability Check ---")
    diagnostics.check_socket_availability()
    
    print("\n--- Test Message Connection ---")
    await diagnostics.send_test_message()
    
    print("\n--- Track Names Retrieval ---")
    await diagnostics.retrieve_track_names()

if __name__ == "__main__":
    asyncio.run(main())

# Diagnostic Instructions:
print("\n--- DIAGNOSTIC INSTRUCTIONS ---")
print("1. Ensure Ableton Live is running")
print("2. Make sure OSC daemon is started")
print("3. Check firewall settings")
print("4. Verify network configuration")
print("5. Restart Ableton Live and OSC daemon if needed")