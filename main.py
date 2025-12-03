import os
import subprocess
import asyncio
import requests
import pickle
import json
import tempfile
import edge_tts
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- SETTINGS ----------------
VIDEO_TITLE = "A knight's truth cuts deeper than any blade. #shorts #storytime #redditstories"
VIDEO_DESCRIPTION = "In the fading light of war, a lone knight speaks the truth no bard dares to sing. His honor, his guilt, his shattered oath… all laid bare before the stranger who dares to listen. These tales are whispered from the ruins of forgotten kingdoms — where loyalty breaks, brotherhood bleeds, and the weight of a single choice can haunt a lifetime.Watch as a weary knight confesses the sins carved into his armor and the sorrow etched into his soul."
VIDEO_CATEGORY = "22"
VIDEO_PRIVACY = "public"
VIDEO_FILENAME = "final_video.mp4"
AUDIO_FILENAME = "voice.mp3"
TOKEN_DIR = "tokens"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

DEEPSEEK_API_KEY = "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"

# Global status tracking
app_status = {
    "stage": "Initializing...",
    "startup_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "video_created": False,
    "video_path": None,
    "video_size_mb": 0,
    "upload_results": [],
    "errors": [],
    "warnings": [],
    "logs": []
}

def log(message, level="INFO"):
    """Add timestamped log entry"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {level}: {message}"
    print(log_entry)
    app_status["logs"].append(log_entry)
    if len(app_status["logs"]) > 100:
        app_status["logs"].pop(0)

def generate_story():
    """Generate story using AI"""
    log("Generating story...")
    prompt = """Generate a highly detailed, emotionally charged Reddit-style confession told in the voice of a weary medieval knight. The story must be around 460 words. Begin with a gripping hook that immediately states the core conflict or moral dilemma. Maintain the diction, vocabulary, and tone of an English nobleman—formal, knightly, and burdened by honor. Fill the narrative with tension, sorrow, and restrained anger, as though the knight is recounting a shameful deed from a war-torn past. The pacing should be human, contemplative, and confessional, as if spoken to a lone traveler beside a dying fire. Focus on moral struggle, loyalty, betrayal, duty, and the suffering of common folk. Do not add any title, formatting, bold, italic, or meta text. Write everything as plain text, as though the knight is speaking directly to the reader asking for advice. Avoid using the word ye."""

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }

    data = {
        "model": "x-ai/grok-4.1-fast:free",
        "messages": [
            {"role": "system", "content": "You are a battle-worn medieval knight confessing the sorrow that has hollowed your heart, speaking with the dignity of a nobleman and the regret of a broken man."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.75
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        result = response.json()
        
        if "choices" in result:
            story = result["choices"][0]["message"]["content"].strip()
            log(f"Story generated: {len(story)} characters")
            return story
        else:
            error_msg = f"API returned unexpected response: {result}"
            log(error_msg, "ERROR")
            app_status["errors"].append(error_msg)
            return None
            
    except Exception as e:
        error_msg = f"Story generation failed: {str(e)}"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return None

async def generate_voice(text, filename=AUDIO_FILENAME):
    """Generate voice from text"""
    log("Generating voice audio...")
    voice_name = "en-GB-RyanNeural"
    
    try:
        communicate = edge_tts.Communicate(text, voice_name, rate="+10%", pitch="-10Hz")
        await communicate.save(filename)
        
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            size_kb = os.path.getsize(filename) / 1024
            log(f"Voice generated: {size_kb:.2f} KB")
            return True
        else:
            error_msg = f"Audio file not created or empty"
            log(error_msg, "ERROR")
            app_status["errors"].append(error_msg)
            return False
            
    except Exception as e:
        error_msg = f"Voice generation failed: {str(e)}"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return False

def make_subtitles(text, duration, chars_per_line=36, words_per_second=2.8):
    """Create SRT subtitles"""
    words = text.split()
    subs = []
    current_line = []
    idx = 1
    start_time = 0
    
    for i, word in enumerate(words):
        current_line.append(word)
        current_text = " ".join(current_line)
        
        if (len(current_text) > chars_per_line and len(current_line) > 1) or i == len(words) - 1:
            word_count = len(current_line)
            line_duration = max(word_count / words_per_second, 1.5)
            end_time = min(start_time + line_duration, duration)
            
            start_srt = format_time_srt(start_time)
            end_srt = format_time_srt(end_time)
            
            subs.append(f"{idx}\n{start_srt} --> {end_srt}\n{current_text}\n")
            
            idx += 1
            start_time = end_time
            current_line = []
    
    return "\n".join(subs)

def format_time_srt(seconds):
    """Format time for SRT"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millisecs = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

def create_video(subtitles_text):
    """Create video with subtitles"""
    log("Creating video...")
    output_file = VIDEO_FILENAME
    
    if not os.path.exists(AUDIO_FILENAME):
        error_msg = f"Audio file {AUDIO_FILENAME} not found"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return False
    
    if not os.path.exists("background.mp4"):
        error_msg = "Background video 'background.mp4' not found"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return False
    
    try:
        audio_info_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", AUDIO_FILENAME]
        audio_info = json.loads(subprocess.check_output(audio_info_cmd).decode())
        audio_duration = float(audio_info["format"]["duration"])
        log(f"Audio duration: {audio_duration:.2f}s")
        
        video_info_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "background.mp4"]
        video_info = json.loads(subprocess.check_output(video_info_cmd).decode())
        video_duration = float(video_info["format"]["duration"])
        
        loop_count = int(audio_duration / video_duration) + 1
        log(f"Background: {video_duration:.2f}s, looping {loop_count} times for audio: {audio_duration:.2f}s")
        
        srt_content = make_subtitles(subtitles_text, audio_duration)
        
        with tempfile.NamedTemporaryFile(suffix=".srt", mode="w", encoding="utf-8", delete=False) as subf:
            subf.write(srt_content)
            sub_path = subf.name
        
        if os.name == 'nt':
            sub_path_escaped = sub_path.replace("\\", "/").replace(":", "\\:")
        else:
            sub_path_escaped = sub_path.replace(":", "\\:")
        
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-stream_loop", str(loop_count),
            "-i", "background.mp4",
            "-i", AUDIO_FILENAME,
            "-filter_complex", f"[0:v]subtitles='{sub_path_escaped}':force_style='FontName=Arial Bold,FontSize=10,Bold=1,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=1,Outline=3,Shadow=2,Alignment=5,MarginV=125'[v]",
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-t", str(audio_duration),
            "-movflags", "+faststart",
            output_file
        ]
        
        log("Running FFmpeg...")
        result = subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
        
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            log(f"Video created: {size_mb:.2f} MB")
            app_status["video_size_mb"] = round(size_mb, 2)
            
            try:
                os.unlink(sub_path)
            except:
                pass
                
            return True
        else:
            error_msg = "Video file not created or empty"
            log(error_msg, "ERROR")
            app_status["errors"].append(error_msg)
            return False
            
    except subprocess.CalledProcessError as e:
        error_msg = f"FFmpeg failed: {e.stderr}"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return False
        
    except Exception as e:
        error_msg = f"Video creation failed: {str(e)}"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return False

def load_credentials(token_path):
    """Load YouTube credentials"""
    with open(token_path, "rb") as f:
        try:
            obj = pickle.load(f)
        except Exception:
            f.close()
            with open(token_path, "r") as jf:
                obj = json.load(jf)

    if isinstance(obj, Credentials):
        return obj
    if isinstance(obj, dict):
        return Credentials.from_authorized_user_info(obj, SCOPES)
    raise ValueError(f"Invalid credentials in {token_path}")

def upload_to_all_accounts(video_path):
    """Upload video to all YouTube accounts"""
    log("Starting uploads to all accounts...")
    
    if not os.path.exists(video_path):
        error_msg = f"Video file {video_path} not found"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return
    
    if not os.path.exists(TOKEN_DIR):
        error_msg = f"Tokens directory '{TOKEN_DIR}' not found"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return
    
    token_files = [f for f in os.listdir(TOKEN_DIR) if f.endswith(('.pkl', '.json'))]
    
    if not token_files:
        error_msg = f"No token files found in '{TOKEN_DIR}'"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        return
    
    log(f"Found {len(token_files)} token file(s)")
    
    for tf in token_files:
        token_path = os.path.join(TOKEN_DIR, tf)
        result = {
            "token_file": tf,
            "status": "pending",
            "video_url": None,
            "error": None
        }
        
        try:
            log(f"Uploading with token: {tf}")
            
            creds = load_credentials(token_path)
            
            if creds.expired and creds.refresh_token:
                log(f"Refreshing expired token: {tf}")
                creds.refresh(Request())
            elif creds.expired:
                raise Exception("Token expired and no refresh token available")
            
            yt = build("youtube", "v3", credentials=creds)
            
            body = {
                "snippet": {
                    "title": VIDEO_TITLE,
                    "description": VIDEO_DESCRIPTION,
                    "tags": ["AI", "YouTube Shorts", "Reddit", "Stories"],
                    "categoryId": VIDEO_CATEGORY
                },
                "status": {
                    "privacyStatus": VIDEO_PRIVACY,
                    "selfDeclaredMadeForKids": False
                }
            }
            
            media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
            resp = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
            
            video_url = f"https://youtu.be/{resp['id']}"
            result["status"] = "success"
            result["video_url"] = video_url
            
            log(f"✅ Upload SUCCESS: {tf} -> {video_url}", "SUCCESS")
            
        except HttpError as e:
            try:
                error_details = json.loads(e.content.decode())
                error_msg = error_details.get('error', {}).get('message', str(e))
                
                if 'quota' in error_msg.lower() or 'exceeded' in error_msg.lower():
                    result["error"] = f"⏰ QUOTA EXCEEDED - Resets at midnight Pacific Time. {error_msg}"
                    log(f"⏰ QUOTA ERROR: {tf} - {error_msg}", "WARNING")
                else:
                    result["error"] = error_msg
                    log(f"❌ Upload FAILED: {tf} - {error_msg}", "ERROR")
            except:
                error_msg = str(e)
                result["error"] = error_msg
                log(f"❌ Upload FAILED: {tf} - {error_msg}", "ERROR")
            
            result["status"] = "failed"
            
        except Exception as e:
            error_msg = str(e)
            result["status"] = "failed"
            result["error"] = error_msg
            log(f"❌ Upload FAILED: {tf} - {error_msg}", "ERROR")
        
        app_status["upload_results"].append(result)
    
    success_count = sum(1 for r in app_status["upload_results"] if r["status"] == "success")
    log(f"Upload complete: {success_count}/{len(token_files)} successful")

async def create_and_upload():
    """Main function to create video and upload to all accounts"""
    try:
        log("Cleaning old files...")
        for f in (AUDIO_FILENAME, VIDEO_FILENAME):
            if os.path.exists(f):
                os.remove(f)
        
        app_status["stage"] = "Generating story..."
        story = generate_story()
        if not story:
            return
        
        app_status["stage"] = "Generating voice..."
        voice_success = await generate_voice(story)
        if not voice_success:
            return
        
        app_status["stage"] = "Creating video..."
        video_success = create_video(story)
        if not video_success:
            return
        
        app_status["video_created"] = True
        app_status["video_path"] = VIDEO_FILENAME
        
        app_status["stage"] = "Uploading to YouTube..."
        upload_to_all_accounts(VIDEO_FILENAME)
        
        app_status["stage"] = "Complete"
        log("All operations complete!")
        
    except Exception as e:
        error_msg = f"Main process failed: {str(e)}"
        log(error_msg, "ERROR")
        app_status["errors"].append(error_msg)
        app_status["stage"] = f"Failed: {str(e)}"

def run_once_async():
    """Wrapper to run async function once"""
    asyncio.run(create_and_upload())

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        total_tokens = len(app_status["upload_results"])
        successful = sum(1 for r in app_status["upload_results"] if r["status"] == "success")
        failed = sum(1 for r in app_status["upload_results"] if r["status"] == "failed")
        
        upload_html = ""
        if app_status["upload_results"]:
            upload_html = "<h3>📤 Upload Results</h3><table style='width:100%; border-collapse: collapse;'>"
            upload_html += "<tr style='background:#eee;'><th style='padding:8px; text-align:left;'>Token File</th><th style='padding:8px; text-align:left;'>Status</th><th style='padding:8px; text-align:left;'>Result</th></tr>"
            
            for result in app_status["upload_results"]:
                status_color = "green" if result["status"] == "success" else "red"
                status_icon = "✅" if result["status"] == "success" else "❌"
                
                result_text = ""
                if result["video_url"]:
                    result_text = f"<a href='{result['video_url']}' target='_blank'>{result['video_url']}</a>"
                elif result["error"]:
                    result_text = result["error"]
                
                upload_html += f"<tr><td style='padding:8px;'>{result['token_file']}</td><td style='padding:8px; color:{status_color};'>{status_icon} {result['status']}</td><td style='padding:8px;'>{result_text}</td></tr>"
            
            upload_html += "</table>"
        
        errors_html = ""
        if app_status["errors"]:
            errors_html = "<div class='errors'><h3>❌ Errors</h3>"
            for error in app_status["errors"][-10:]:
                errors_html += f"<p style='margin:5px 0; color:red;'>{error}</p>"
            errors_html += "</div>"
        
        logs_html = "<div class='logs'><h3>📋 Recent Logs</h3><div style='background:#f9f9f9; padding:10px; max-height:300px; overflow-y:auto; font-family:monospace; font-size:12px;'>"
        for log_entry in app_status["logs"][-50:]:
            logs_html += f"<div>{log_entry}</div>"
        logs_html += "</div></div>"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Reddit Video Generator</title>
            <meta http-equiv="refresh" content="30">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #f4f4f4; }}
                .container {{ background: white; padding: 30px; border-radius: 10px; max-width: 1200px; margin: 0 auto; }}
                .stats {{ background: #e8f4f8; padding: 20px; border-radius: 5px; margin: 20px 0; }}
                .stat-row {{ display: flex; justify-content: space-between; margin: 10px 0; }}
                .errors {{ background: #ffeaea; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .logs {{ background: #f0f0f0; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                table {{ margin: 20px 0; }}
                th {{ background: #ddd; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🎬 Reddit Video Generator</h1>
                
                <div class="stats">
                    <h3>📊 Status Dashboard</h3>
                    <div class="stat-row"><strong>Current Stage:</strong> <span>{app_status['stage']}</span></div>
                    <div class="stat-row"><strong>Started At:</strong> <span>{app_status['startup_time']}</span></div>
                    <div class="stat-row"><strong>Video Created:</strong> <span>{'✅ Yes' if app_status['video_created'] else '❌ No'}</span></div>
                    {f"<div class='stat-row'><strong>Video Size:</strong> <span>{app_status['video_size_mb']} MB</span></div>" if app_status['video_size_mb'] else ''}
                    <div class="stat-row"><strong>Uploads:</strong> <span style='color:green;'>✅ {successful}</span> / <span style='color:red;'>❌ {failed}</span> / <span>Total: {total_tokens}</span></div>
                </div>
                
                {upload_html}
                
                {errors_html}
                
                {logs_html}
                
                <p style="margin-top:30px;"><em>Page auto-refreshes every 30 seconds</em></p>
            </div>
        </body>
        </html>
        """
        
        self.wfile.write(html_content.encode())
    
    def log_message(self, format, *args):
        pass

def run_web_server():
    """Run the web server"""
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    log(f"Web server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    print("="*60)
    print("🎬 REDDIT VIDEO GENERATOR - SINGLE RUN MODE")
    print("="*60)
    
    log("Starting video creation and upload process...")
    video_thread = threading.Thread(target=run_once_async, daemon=True)
    video_thread.start()
    
    run_web_server()