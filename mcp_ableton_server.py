from mcp.server.fastmcp import FastMCP
import asyncio
import json
import socket
import sys
from typing import List, Optional, Dict, Any
import os

class AbletonClient:
    def __init__(self, host='127.0.0.1', port=65432):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False
        self.responses = {}  # Store futures keyed by (request_id)
        self.lock = asyncio.Lock()
        self._request_id = 0  # counter to generate unique ids
        
        # Async task for reading responses
        self.response_task = None

    async def start_response_reader(self):
        """Background task to read responses from the socket."""
        # Convert self.sock to asyncio Streams
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        await loop.create_connection(lambda: protocol, sock=self.sock)

        buffer = b''
        while self.connected:
            try:
                # Read data from socket
                data = await reader.read(8192)
                if not data:
                    # Connection closed
                    break

                # Add to buffer
                buffer += data
                
                # Process complete messages from buffer
                messages, buffer = self._extract_messages(buffer)
                
                for msg in messages:
                    try:
                        parsed_msg = json.loads(msg.decode())
                        await self._process_message(parsed_msg)
                    except json.JSONDecodeError as e:
                        print(f"Invalid JSON: {e}", file=sys.stderr)
                        print(f"Problem message: {msg[:100]}...", file=sys.stderr)
                    except Exception as e:
                        print(f"Error processing message: {e}", file=sys.stderr)

            except Exception as e:
                print(f"Error reading response: {e}", file=sys.stderr)
                break

    def _extract_messages(self, buffer):
        """Extract complete JSON messages from buffer."""
        messages = []
        start_idx = 0
        
        while True:
            # Find start of a JSON object
            if start_idx >= len(buffer):
                return messages, b''
                
            obj_start = buffer.find(b'{', start_idx)
            if obj_start == -1:
                # No more objects in buffer
                return messages, b''
            
            # Find matching closing brace
            brace_count = 1
            i = obj_start + 1
            
            while i < len(buffer) and brace_count > 0:
                if buffer[i] == ord(b'{'):
                    brace_count += 1
                elif buffer[i] == ord(b'}'):
                    brace_count -= 1
                i += 1
                
            if brace_count == 0:
                # Found complete JSON object
                messages.append(buffer[obj_start:i])
                start_idx = i
            else:
                # Incomplete JSON object
                return messages, buffer[obj_start:]

    async def _process_message(self, msg):
        """Process a single JSON message."""
        # If it's a JSON-RPC response
        resp_id = msg.get('id')
        if 'result' in msg or 'error' in msg:
            # Response to a request
            async with self.lock:
                fut = self.responses.pop(resp_id, None)
            if fut and not fut.done():
                fut.set_result(msg)
        else:
            # Otherwise it's an "osc_response" or another type
            if msg.get('type') == 'osc_response':
                # We can route according to the address
                address = msg.get('address')
                args = msg.get('args')
                await self.handle_osc_response(address, args)
            else:
                print(f"Unknown message: {msg}", file=sys.stderr)

    async def handle_osc_response(self, address: str, args):
        """Callback when receiving an OSC message from Ableton."""
        print(f"OSC Notification from {address}: {args}", file=sys.stderr)

    def connect(self):
        """Connect to the OSC daemon via TCP socket."""
        if not self.connected:
            try:
                self.sock.connect((self.host, self.port))
                self.connected = True
                
                # Start the response reader task
                self.response_task = asyncio.create_task(self.start_response_reader())
                return True
            except Exception as e:
                print(f"Failed to connect to daemon: {e}", file=sys.stderr)
                return False
        return True

    async def send_rpc_request(self, method: str, params: dict) -> dict:
        """
        Send a JSON-RPC request (method, params) and wait for the response.
        """
        if not self.connected:
            if not self.connect():
                return {'status': 'error', 'message': 'Not connected to daemon'}

        # Generate a unique ID
        self._request_id += 1
        request_id = str(self._request_id)

        # Build the JSON-RPC request
        request_obj = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }

        future = asyncio.Future()
        async with self.lock:
            self.responses[request_id] = future

        try:
            # Convert to JSON and send
            json_data = json.dumps(request_obj)
            self.sock.sendall(json_data.encode())

            # Wait for the JSON-RPC response
            try:
                msg = await asyncio.wait_for(future, timeout=5.0)
            except asyncio.TimeoutError:
                async with self.lock:
                    self.responses.pop(request_id, None)
                return {'status': 'error', 'message': 'Response timeout'}

            # Check if we have a 'result' or an 'error'
            if 'error' in msg:
                return {
                    'status': 'error',
                    'code': msg['error'].get('code'),
                    'message': msg['error'].get('message')
                }
            else:
                return {
                    'status': 'ok',
                    'result': msg.get('result')
                }

        except Exception as e:
            self.connected = False
            return {'status': 'error', 'message': str(e)}

    async def close(self):
        """Close the connection."""
        if self.connected:
            self.connected = False
            if self.response_task:
                self.response_task.cancel()
                try:
                    await self.response_task
                except asyncio.CancelledError:
                    pass
            self.sock.close()

# Initialize the MCP server
mcp = FastMCP("Ableton Live Controller", dependencies=["python-osc"])

# Create Ableton client
ableton_client = AbletonClient()

# ----- TRACK MANAGEMENT TOOLS -----

@mcp.tool()
async def get_track_names(index_min: Optional[int] = None, index_max: Optional[int] = None) -> str:
    """
    Get the names of tracks in Ableton Live.
    
    Args:
        index_min: Optional minimum track index
        index_max: Optional maximum track index
    
    Returns:
        A formatted string containing track names
    """
    params = {
        "address": "/live/song/get/track_names",
        "args": []
    }
    
    if index_min is not None and index_max is not None:
        params["args"] = [index_min, index_max]

    response = await ableton_client.send_rpc_request("send_message", params)
    if response['status'] == 'ok':
        result = response['result']
        if result.get('status') == 'success':
            track_names = result.get('data', [])
            if not track_names:
                return "No tracks found"
            
            # Format the track names
            formatted_tracks = []
            for idx, name in enumerate(track_names):
                formatted_tracks.append(f"{idx}: {name}")
            
            return "Track Names:\n" + "\n".join(formatted_tracks)
        else:
            return f"Error: {result.get('message', 'Unknown error')}"
    else:
        return f"Error getting track names: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_tracks(config_file: Optional[str] = None) -> str:
    """
    Create tracks in Ableton Live based on a configuration file or default template.
    
    Args:
        config_file: Optional path to a JSON configuration file
    
    Returns:
        A message indicating the result of the operation
    """
    # Define default track configuration for dubstep/metal fusion
    # Simplified to reduce message size
    default_config = [
        {"name": "Kick", "type": "audio"},
        {"name": "Snare", "type": "audio"},
        {"name": "Hi-Hats", "type": "audio"},
        {"name": "Crash/Ride", "type": "audio"},
        {"name": "Percussion", "type": "audio"},
        {"name": "Sub Bass", "type": "midi"},
        {"name": "Wobble Bass", "type": "midi"},
        {"name": "Metallic Bass", "type": "midi"},
        {"name": "Pads", "type": "midi"},
        {"name": "Lead", "type": "midi"},
        {"name": "FX", "type": "audio"},
        {"name": "Vocals", "type": "audio"}
    ]
    
    # Load configuration from file if provided
    track_config = default_config
    if config_file and os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                track_config = json.load(f)
        except Exception as e:
            return f"Error loading config file: {str(e)}"
    
    # Send track creation request
    response = await ableton_client.send_rpc_request("create_tracks", {"track_config": track_config})
    
    if response['status'] == 'ok':
        result = response['result']
        return f"Successfully created {len(track_config)} tracks for dubstep/metal fusion"
    else:
        return f"Error creating tracks: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def setup_live_set(bpm: int = 150, key: str = "F Minor") -> str:
    """
    Set up a complete dubstep/metal fusion live set.
    
    Args:
        bpm: Beats per minute (default: 150)
        key: Musical key (default: F Minor)
    
    Returns:
        A message indicating the result of the operation
    """
    # Send BPM change request
    tempo_params = {
        "address": "/live/song/set/tempo",
        "args": [bpm]
    }
    tempo_response = await ableton_client.send_rpc_request("send_message", tempo_params)
    
    # Create tracks (simplified)
    tracks_response = await create_tracks(None)
    
    # Create scenes one by one instead of in a batch
    scenes = ["INTRO", "DROP 1", "BREAKDOWN", "BUILD-UP", "DROP 2", "BRIDGE", "FINAL BUILD", "FINAL DROP", "OUTRO"]
    
    for i, scene_name in enumerate(scenes):
        scene_params = {
            "address": "/live/song/create/scene",
            "args": [i]
        }
        await ableton_client.send_rpc_request("send_message", scene_params)
        
        name_params = {
            "address": "/live/scene/set/name",
            "args": [i, scene_name]
        }
        await ableton_client.send_rpc_request("send_message", name_params)
    
    return f"Live set created at {bpm} BPM in {key} with {len(scenes)} scenes"

# ----- SOUND DESIGN TOOLS -----

@mcp.tool()
async def create_wobble_bass(oscillator_type: str = "square-saw", lfo_rate: str = "1/4", resonance: int = 40) -> str:
    """
    Create a wobble bass sound on the selected track.
    
    Args:
        oscillator_type: Type of oscillator (square-saw, modern-talking, etc.)
        lfo_rate: LFO rate (1/4, 1/8, 1/16, etc.)
        resonance: Filter resonance (0-100)
    
    Returns:
        A message indicating the result of the operation
    """
    # In a real implementation, we would need to select an appropriate track
    # and apply device effects. For now, we'll just simulate this behavior
    
    wobble_track = 6  # Assuming track 6 is designated for wobble bass
    
    # Set up parameters for wobble bass
    params = {
        "address": "/live/device/set/parameters",
        "args": [wobble_track, 0, oscillator_type, lfo_rate, resonance]
    }
    
    response = await ableton_client.send_rpc_request("send_message", params)
    
    if response['status'] == 'ok':
        return f"Created wobble bass with {oscillator_type} oscillator, {lfo_rate} LFO rate, and {resonance}% resonance"
    else:
        return f"Error creating wobble bass: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def create_metallic_bass(modulation_amount: int = 50, comb_filter: bool = True, distortion_amount: int = 60) -> str:
    """
    Create a metallic bass sound on the selected track.
    
    Args:
        modulation_amount: FM modulation amount (0-100)
        comb_filter: Whether to use comb filter for metallic resonance
        distortion_amount: Distortion amount (0-100)
    
    Returns:
        A message indicating the result of the operation
    """
    # In a real implementation, we would select an appropriate track
    # and apply device effects. For now, we'll just simulate this behavior
    
    metallic_track = 8  # Assuming track 8 is designated for metallic bass
    
    # Set up parameters for metallic bass
    params = {
        "address": "/live/device/set/parameters",
        "args": [metallic_track, 0, modulation_amount, 1 if comb_filter else 0, distortion_amount]
    }
    
    response = await ableton_client.send_rpc_request("send_message", params)
    
    if response['status'] == 'ok':
        return f"Created metallic bass with {modulation_amount}% modulation, {'with' if comb_filter else 'without'} comb filter, and {distortion_amount}% distortion"
    else:
        return f"Error creating metallic bass: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def load_drum_pattern(pattern_type: str = "half-time", variation: int = 1) -> str:
    """
    Load a specific drum pattern onto drum tracks.
    
    Args:
        pattern_type: Type of pattern (half-time, double-kick, hybrid, breakdown, build-up, glitch)
        variation: Pattern variation number (1-3)
    
    Returns:
        A message indicating the result of the operation
    """
    # Define the track indices for drum elements
    kick_track = 0
    snare_track = 1
    hats_track = 2
    
    # Dictionary mapping pattern types to appropriate OSC addresses
    pattern_addresses = {
        "half-time": "/live/clip/load_pattern/half_time",
        "double-kick": "/live/clip/load_pattern/double_kick",
        "hybrid": "/live/clip/load_pattern/hybrid",
        "breakdown": "/live/clip/load_pattern/breakdown",
        "build-up": "/live/clip/load_pattern/build_up",
        "glitch": "/live/clip/load_pattern/glitch"
    }
    
    if pattern_type not in pattern_addresses:
        return f"Unknown pattern type: {pattern_type}. Available types: {', '.join(pattern_addresses.keys())}"
    
    # Get the appropriate address for the pattern
    address = pattern_addresses[pattern_type]
    
    # Load pattern to each drum track
    results = []
    for track in [kick_track, snare_track, hats_track]:
        params = {
            "address": address,
            "args": [track, variation]
        }
        
        response = await ableton_client.send_rpc_request("send_message", params)
        
        if response['status'] == 'ok':
            results.append(f"Loaded {pattern_type} pattern on track {track}")
        else:
            results.append(f"Error loading pattern on track {track}: {response.get('message', 'Unknown error')}")
    
    return "\n".join(results)

# ----- PERFORMANCE TOOLS -----

@mcp.tool()
async def trigger_scene(scene_index: int) -> str:
    """
    Trigger a specific scene in the live set.
    
    Args:
        scene_index: Index of the scene to trigger
    
    Returns:
        A message indicating the result of the operation
    """
    params = {
        "address": "/live/scene/fire",
        "args": [scene_index]
    }
    
    response = await ableton_client.send_rpc_request("send_message", params)
    
    if response['status'] == 'ok':
        return f"Triggered scene {scene_index}"
    else:
        return f"Error triggering scene: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def filter_sweep(track_index: Optional[int] = None, direction: str = "up", duration_beats: int = 8) -> str:
    """
    Perform a filter sweep on a track or the master track.
    
    Args:
        track_index: Index of the track (None for master)
        direction: Direction of sweep ("up" or "down")
        duration_beats: Duration of sweep in beats
    
    Returns:
        A message indicating the result of the operation
    """
    # Determine which track to use (master if None)
    target_track = -1 if track_index is None else track_index
    
    # Calculate frequencies based on direction
    if direction.lower() == "up":
        start_freq = 100
        end_freq = 20000
    else:
        start_freq = 20000
        end_freq = 100
    
    params = {
        "address": "/live/track/filter_sweep",
        "args": [target_track, start_freq, end_freq, duration_beats]
    }
    
    response = await ableton_client.send_rpc_request("send_message", params)
    
    if response['status'] == 'ok':
        track_desc = "master track" if target_track == -1 else f"track {target_track}"
        return f"Performing {direction} filter sweep on {track_desc} over {duration_beats} beats"
    else:
        return f"Error performing filter sweep: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def set_wobble_rate(rate: str = "1/4") -> str:
    """
    Set the wobble bass LFO rate for all active wobble bass tracks.
    
    Args:
        rate: LFO rate (1/4, 1/8, 1/16, etc.)
    
    Returns:
        A message indicating the result of the operation
    """
    # In a real implementation, find all tracks with wobble bass devices
    wobble_track = 6  # Assuming track 6 is the wobble bass track
    
    params = {
        "address": "/live/device/set/parameter_by_name",
        "args": [wobble_track, 0, "LFO Rate", rate]
    }
    
    response = await ableton_client.send_rpc_request("send_message", params)
    
    if response['status'] == 'ok':
        return f"Set wobble rate to {rate} on track {wobble_track}"
    else:
        return f"Error setting wobble rate: {response.get('message', 'Unknown error')}"

@mcp.tool()
async def glitch_effect(intensity: int = 50, duration_beats: int = 4) -> str:
    """
    Apply a glitch effect to the selected tracks.
    
    Args:
        intensity: Effect intensity (0-100)
        duration_beats: Duration of effect in beats
    
    Returns:
        A message indicating the result of the operation
    """
    # In a real implementation, we would apply to the currently selected tracks
    # For now, apply to a fixed track
    glitch_track = 10  # Assuming track 10 is for FX
    
    params = {
        "address": "/live/track/glitch_effect",
        "args": [glitch_track, intensity, duration_beats]
    }
    
    response = await ableton_client.send_rpc_request("send_message", params)
    
    if response['status'] == 'ok':
        return f"Applied {intensity}% glitch effect to track {glitch_track} for {duration_beats} beats"
    else:
        return f"Error applying glitch effect: {response.get('message', 'Unknown error')}"

# Run the MCP server
if __name__ == "__main__":
    try:
        mcp.run()
    finally:
        asyncio.run(ableton_client.close())