# osc_daemon.py
import asyncio
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.dispatcher import Dispatcher
import json
from typing import Optional, Dict, Any, List, Tuple

class AbletonOSCDaemon:
    def __init__(self, 
                 socket_host='127.0.0.1', socket_port=65432,
                 ableton_host='127.0.0.1', ableton_port=11000,
                 receive_port=11001):
        self.socket_host = socket_host
        self.socket_port = socket_port
        self.ableton_host = ableton_host
        self.ableton_port = ableton_port
        self.receive_port = receive_port
        
        # Initialize OSC client for Ableton
        self.osc_client = SimpleUDPClient(ableton_host, ableton_port)
        
        # Store active connections waiting for responses
        self.pending_responses: Dict[str, asyncio.Future] = {}
        
        # Initialize OSC server dispatcher
        self.dispatcher = Dispatcher()
        self.dispatcher.set_default_handler(self.handle_ableton_message)
        
        # Track state information
        self.track_info = {}
        self.clip_info = {}
        self.device_info = {}
        
        # Buffer for handling large messages
        self.message_buffers = {}
        
    def handle_ableton_message(self, address: str, *args):
        """Handle incoming OSC messages from Ableton."""
        print(f"[ABLETON MESSAGE] Address: {address}, Args: {args}")
        
        # Update internal state based on message type
        if address.startswith('/live/track'):
            self._update_track_info(address, args)
        elif address.startswith('/live/clip'):
            self._update_clip_info(address, args)
        elif address.startswith('/live/device'):
            self._update_device_info(address, args)
            
        # If this address has a pending response, resolve it
        if address in self.pending_responses:
            future = self.pending_responses[address]
            if not future.done():
                future.set_result({
                    'status': 'success',
                    'address': address,
                    'data': args
                })
            del self.pending_responses[address]
    
    def _update_track_info(self, address: str, args):
        """Update internal track information based on OSC messages."""
        parts = address.split('/')
        if len(parts) >= 4:
            # Example: /live/track/info
            track_action = parts[3]
            
            if track_action == 'name' and len(args) >= 2:
                track_index = args[0]
                track_name = args[1]
                if 'names' not in self.track_info:
                    self.track_info['names'] = {}
                self.track_info['names'][track_index] = track_name
    
    def _update_clip_info(self, address: str, args):
        """Update internal clip information based on OSC messages."""
        # Similar structure to track info updates
        pass
    
    def _update_device_info(self, address: str, args):
        """Update internal device information based on OSC messages."""
        # Similar structure to track info updates
        pass
            
    async def start(self):
        """Start both the socket server and OSC server."""
        # Start OSC server to receive Ableton messages
        self.osc_server = AsyncIOOSCUDPServer(
            (self.socket_host, self.receive_port),
            self.dispatcher,
            asyncio.get_event_loop()
        )
        await self.osc_server.create_serve_endpoint()
        
        # Start socket server for MCP communication
        server = await asyncio.start_server(
            self.handle_socket_client,
            self.socket_host,
            self.socket_port
        )
        print(f"Ableton OSC Daemon listening on {self.socket_host}:{self.socket_port}")
        print(f"OSC Server receiving on {self.socket_host}:{self.receive_port}")
        print(f"Sending to Ableton on {self.ableton_host}:{self.ableton_port}")
        
        async with server:
            await server.serve_forever()
            
    async def handle_socket_client(self, reader, writer):
        """Handle incoming socket connections from MCP server."""
        client_address = writer.get_extra_info('peername')
        print(f"[NEW CONNECTION] Client connected from {client_address}")
        
        # Create a buffer for this client
        buffer_key = f"{client_address[0]}:{client_address[1]}"
        self.message_buffers[buffer_key] = b''
        
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                
                # Add to buffer
                self.message_buffers[buffer_key] += data
                
                # Try to process complete JSON messages
                await self._process_buffer(buffer_key, writer)
                    
        except Exception as e:
            print(f"[CONNECTION ERROR] Error handling client: {e}")
        finally:
            # Clean up buffer
            if buffer_key in self.message_buffers:
                del self.message_buffers[buffer_key]
            
            writer.close()
            await writer.wait_closed()
            print(f"[CONNECTION CLOSED] Client {client_address} disconnected")
    
    async def _process_buffer(self, buffer_key, writer):
        """Process buffered data, extracting complete JSON messages."""
        buffer = self.message_buffers[buffer_key]
        
        # Check if buffer contains complete JSON objects
        start_index = 0
        while start_index < len(buffer):
            try:
                # Find the next opening brace if we're not at the start
                if start_index > 0:
                    next_open = buffer.find(b'{', start_index)
                    if next_open == -1:
                        # No more JSON objects in buffer
                        self.message_buffers[buffer_key] = b''
                        return
                    start_index = next_open
                
                # Try to decode a complete JSON object
                json_obj = json.loads(buffer[start_index:].decode('utf-8'))
                
                # If successful, process the message
                await self._handle_message(json_obj, writer)
                
                # Update buffer to remove processed message
                end_index = buffer.find(b'}', start_index) + 1
                self.message_buffers[buffer_key] = buffer[end_index:]
                buffer = self.message_buffers[buffer_key]
                start_index = 0
            
            except json.JSONDecodeError:
                # Incomplete JSON, wait for more data
                break
            
            except Exception as e:
                print(f"[MESSAGE PROCESSING ERROR] {e}")
                # Skip this message and try to continue
                end_index = buffer.find(b'}', start_index)
                if end_index == -1:
                    # No closing brace, reset buffer
                    self.message_buffers[buffer_key] = b''
                    return
                    
                start_index = end_index + 1
    
    async def _handle_message(self, message, writer):
        """Handle a complete JSON message."""
        print(f"[RECEIVED MESSAGE] {message}")
        
        # JSON-RPC support: use method or command
        command = message.get('method') or message.get('command')
        
        if command == 'send_message':
            # For JSON-RPC, params might be a dict
            params = message.get('params', {})
            address = params.get('address') if isinstance(params, dict) else None
            args = params.get('args', []) if isinstance(params, dict) else []
            
            # For commands that expect responses, set up a future
            if address and address.startswith(('/live/device/get', '/live/scene/get', '/live/view/get', '/live/clip/get', '/live/clip_slot/get', '/live/track/get', '/live/song/get', '/live/api/get', '/live/application/get', '/live/test', '/live/error')):
                # Create response future with timeout
                future = asyncio.Future()
                self.pending_responses[address] = future

                # Send to Ableton
                self.osc_client.send_message(address, args)

                try:
                    # Wait for response with timeout
                    response = await asyncio.wait_for(future, timeout=5.0)
                    print(f"[OSC RESPONSE] Received: {response}")
                    response_to_send = {
                        'jsonrpc': '2.0',
                        'id': message.get('id'),
                        'result': response
                    }
                    writer.write(json.dumps(response_to_send).encode())
                except asyncio.TimeoutError:
                    error_response = {
                        'jsonrpc': '2.0',
                        'id': message.get('id'),
                        'error': {
                            'code': -32000,
                            'message': f'Timeout waiting for response to {address}'
                        }
                    }
                    print(f"[OSC TIMEOUT] {error_response}")
                    writer.write(json.dumps(error_response).encode())
                    
            else:
                # For commands that don't expect responses
                self.osc_client.send_message(address, args)
                response = {
                    'jsonrpc': '2.0',
                    'id': message.get('id'),
                    'result': {'status': 'sent'}
                }
                writer.write(json.dumps(response).encode())
        
        elif command == 'create_tracks':
            # Handle creating tracks from a template
            params = message.get('params', {})
            track_config = params.get('track_config', [])
            
            # Process track creation
            result = await self._create_tracks(track_config)
            
            response = {
                'jsonrpc': '2.0',
                'id': message.get('id'),
                'result': result
            }
            writer.write(json.dumps(response).encode())
        
        elif command == 'setup_live_set':
            # Handle setting up the live set from a template
            params = message.get('params', {})
            set_config = params.get('set_config', {})
            
            # Process live set setup
            result = await self._setup_live_set(set_config)
            
            response = {
                'jsonrpc': '2.0',
                'id': message.get('id'),
                'result': result
            }
            writer.write(json.dumps(response).encode())
        
        elif command == 'get_track_names':
            # Handle getting track names
            params = message.get('params', {})
            index_min = params.get('index_min')
            index_max = params.get('index_max')
            
            # Query track names from Ableton
            result = await self._get_track_names(index_min, index_max)
            
            response = {
                'jsonrpc': '2.0',
                'id': message.get('id'),
                'result': result
            }
            writer.write(json.dumps(response).encode())
            
        elif command == 'get_status':
            response = {
                'jsonrpc': '2.0',
                'id': message.get('id'),
                'result': {
                    'status': 'ok',
                    'ableton_port': self.ableton_port,
                    'receive_port': self.receive_port
                }
            }
            print(f"[STATUS REQUEST] Responding with: {response}")
            writer.write(json.dumps(response).encode())
        else:
            error_response = {
                'jsonrpc': '2.0',
                'id': message.get('id'),
                'error': {
                    'code': -32601,
                    'message': 'Method not found'
                }
            }
            print(f"[UNKNOWN COMMAND] Received: {message}")
            writer.write(json.dumps(error_response).encode())
        
        await writer.drain()
    
    async def _create_tracks(self, track_config: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create tracks in Ableton Live based on a configuration."""
        results = []
        
        for track_idx, track in enumerate(track_config):
            track_name = track.get('name', f'Track {track_idx+1}')
            track_type = track.get('type', 'audio')  # audio, midi, return, master
            
            # Create track based on type
            if track_type == 'audio':
                self.osc_client.send_message('/live/song/create/audio_track', [track_idx])
            elif track_type == 'midi':
                self.osc_client.send_message('/live/song/create/midi_track', [track_idx])
            
            # Wait a bit for track creation
            await asyncio.sleep(0.1)
            
            # Set track name
            self.osc_client.send_message('/live/track/set/name', [track_idx, track_name])
            
            # Add devices if specified
            devices = track.get('devices', [])
            for dev_idx, device in enumerate(devices):
                device_name = device.get('name', '')
                # Add device - simplified, would need to be expanded for real implementation
                self.osc_client.send_message('/live/track/add_device_by_name', [track_idx, device_name])
                
                # Wait for device creation
                await asyncio.sleep(0.1)
                
                # Set device parameters if specified
                params = device.get('parameters', {})
                for param_name, param_value in params.items():
                    # This is simplified, real implementation would need proper parameter indexing
                    self.osc_client.send_message('/live/device/set/parameter_by_name', 
                                                 [track_idx, dev_idx, param_name, param_value])
            
            results.append({
                "track_index": track_idx,
                "name": track_name,
                "status": "created"
            })
            
        return {
            "status": "success",
            "tracks_created": len(results),
            "track_details": results
        }
    
    async def _setup_live_set(self, set_config: Dict[str, Any]) -> Dict[str, Any]:
        """Set up a complete live set based on a configuration."""
        # Set BPM
        bpm = set_config.get('bpm', 120)
        self.osc_client.send_message('/live/song/set/tempo', [bpm])
        
        # Create tracks
        tracks = set_config.get('tracks', [])
        track_results = {"status": "success", "tracks_created": len(tracks)}
        
        for track_idx, track in enumerate(tracks):
            track_name = track.get('name', f'Track {track_idx+1}')
            track_type = track.get('type', 'audio')
            
            # Create track based on type
            if track_type == 'audio':
                self.osc_client.send_message('/live/song/create/audio_track', [])
            elif track_type == 'midi':
                self.osc_client.send_message('/live/song/create/midi_track', [])
            
            # Wait a bit for track creation
            await asyncio.sleep(0.1)
            
            # Set track name
            self.osc_client.send_message('/live/track/set/name', [track_idx, track_name])
        
        # Create scenes
        scenes = set_config.get('scenes', [])
        scene_results = []
        
        for scene_idx, scene in enumerate(scenes):
            scene_name = scene.get('name', f'Scene {scene_idx+1}')
            
            # Create scene
            self.osc_client.send_message('/live/song/create/scene', [scene_idx])
            
            # Set scene name
            self.osc_client.send_message('/live/scene/set/name', [scene_idx, scene_name])
            
            scene_results.append({
                "scene_index": scene_idx,
                "name": scene_name,
                "status": "created"
            })
        
        # Return combined results
        return {
            "status": "success",
            "bpm": bpm,
            "tracks": track_results,
            "scenes": scene_results
        }
    
    async def _get_track_names(self, index_min: Optional[int] = None, index_max: Optional[int] = None) -> Dict[str, Any]:
        """Get track names from Ableton Live."""
        # Create response future
        future = asyncio.Future()
        address = '/live/song/get/track_names'
        self.pending_responses[address] = future
        
        # Send request to Ableton
        args = []
        if index_min is not None and index_max is not None:
            args = [index_min, index_max]
        
        self.osc_client.send_message(address, args)
        
        try:
            # Wait for response with timeout
            response = await asyncio.wait_for(future, timeout=5.0)
            # Extract track names from the response
            if isinstance(response.get('data'), tuple) and len(response.get('data', ())) > 0:
                track_names = response.get('data')
            else:
                track_names = []
            
            # Format the response
            result = {
                "status": "success",
                "track_names": track_names
            }
            return result
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "message": "Timeout waiting for track names"
            }

if __name__ == "__main__":
    daemon = AbletonOSCDaemon()
    asyncio.run(daemon.start())