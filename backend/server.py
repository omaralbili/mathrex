import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mathrex")

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "index.html" if (BASE_DIR / "index.html").exists() else BASE_DIR.parent / "index.html"
STATIC_DIR = BASE_DIR / "static" if (BASE_DIR / "static").exists() else BASE_DIR.parent / "static"

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-3.8-live").strip()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

ALLOWED_VOICES = {"Puck", "Aoede", "Kore", "Charon", "Fenrir", "Leda", "Orus", "Zephyr"}
DEFAULT_VOICE = "Puck"

MATHREX_SYSTEM_INSTRUCTION = (
    "أنت MathRex (ماث ريكس)، مدرس ومساعد رياضيات ذكي وودود جداً. "
    "اتكلم دايماً بالعربي المصري الطبيعي السهل والقريب من الطالب. "
    "مهمتك الأساسية إنك تجاوب على أي سؤال في الرياضيات بدقة 100% — سواء حساب، جبر، هندسة، "
    "حساب مثلثات، تفاضل وتكامل، إحصاء، احتمالات، أو مسائل كلامية لكل المراحل (ابتدائي، إعدادي، ثانوي، جامعة). "
    "قواعد مهمة جداً في ردودك الصوتية:\n"
    "1. تأكد من صحة الحسابات والخطوات كويس جداً قبل ما تقول النتيجة النهائية.\n"
    "2. اشرح الحل خطوة بخطوة ببساطة ووضوح، ومن غير ما تطوّل زيادة عن اللزوم عشان المحادثة الصوتية تكون سريعة وممتعة.\n"
    "3. انطق الرموز الرياضية بشكل طبيعي بالعربي المصري (مثلاً قول 'س تربيع' أو 'إكس تربيع'، 'جذر'، 'على'، 'في'، 'بيساوي') وما تنطقش رموز برمجية.\n"
    "4. لو الطالب بعتلك صورة مسألة أو سؤال (مكتوب بخط اليد أو مطبوع أو رسم هندسي أو اختيار من متعدد)، "
    "اقرأ الأرقام والرموز بدقة شديدة وحل المسألة خطوة بخطوة وقول الإجابة النهائية بوضوح بالعربي المصري.\n"
    "5. لو الطالب قاطعك في نص الكلام، اقف فوراً واسمع سؤاله أو تعليقه الجديد ورد عليه مباشرة.\n"
    "6. شجع الطالب دايماً بأسلوب لطيف ومحفز، ولو سألك سؤال بعيد عن الرياضيات رد عليه بلطف وفكّره إنك متخصص في الرياضيات ومستعد تساعده في أي مسألة."
)


def build_gemini_ws_url() -> str:
    query = urlencode({"key": API_KEY})
    return (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService."
        f"BidiGenerateContent?{query}"
    )


def build_setup_payload(voice_name: str) -> dict:
    return {
        "setup": {
            "model": GEMINI_MODEL,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice_name,
                        }
                    }
                },
            },
            "systemInstruction": {
                "parts": [{"text": MATHREX_SYSTEM_INSTRUCTION}],
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
    }


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(INDEX_HTML)


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "app": "MathRex AI Live",
            "model": GEMINI_MODEL,
            "api_key_configured": bool(API_KEY),
        }
    )


async def live_ws_proxy(request: web.Request) -> web.WebSocketResponse:
    client_ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=10 * 1024 * 1024)
    await client_ws.prepare(request)

    if not API_KEY:
        await client_ws.send_json({"error": {"message": "GEMINI_API_KEY is not configured on the server."}})
        await client_ws.close(code=1011, message=b"Missing API key")
        return client_ws

    requested_voice = request.query.get("voice", DEFAULT_VOICE)
    voice_name = requested_voice if requested_voice in ALLOWED_VOICES else DEFAULT_VOICE

    gemini_url = build_gemini_ws_url()
    timeout = ClientTimeout(total=None, sock_connect=20, sock_read=None)

    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.ws_connect(
                gemini_url,
                heartbeat=20.0,
                max_msg_size=10 * 1024 * 1024,
            ) as gemini_ws:
                logger.info("Connected to Gemini Live API (%s, voice=%s)", GEMINI_MODEL, voice_name)
                await gemini_ws.send_json(build_setup_payload(voice_name))

                async def client_to_gemini() -> None:
                    async for msg in client_ws:
                        if msg.type == WSMsgType.TEXT:
                            await gemini_ws.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            await gemini_ws.send_bytes(msg.data)
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                            break

                async def gemini_to_client() -> None:
                    async for msg in gemini_ws:
                        if msg.type == WSMsgType.TEXT:
                            await client_ws.send_str(msg.data)
                        elif msg.type == WSMsgType.BINARY:
                            # Gemini Live API sends UTF-8 JSON payloads as binary frames.
                            # Decode to text so the browser always receives clean JSON strings.
                            await client_ws.send_str(msg.data.decode("utf-8"))
                        elif msg.type == WSMsgType.ERROR:
                            logger.error("Gemini WS error: %s", gemini_ws.exception())
                            break
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING):
                            logger.info(
                                "Gemini WS closed: code=%s data=%s",
                                gemini_ws.close_code,
                                msg.data,
                            )
                            break

                tasks = [
                    asyncio.create_task(client_to_gemini()),
                    asyncio.create_task(gemini_to_client()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc

    except Exception as exc:
        logger.exception("Live proxy error: %s", exc)
        if not client_ws.closed:
            try:
                await client_ws.send_json({"error": {"message": f"Connection error: {exc}"}})
            except Exception:
                pass
    finally:
        if not client_ws.closed:
            await client_ws.close()
        logger.info("Client live session closed")

    return client_ws


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws/live", live_ws_proxy)
    if STATIC_DIR.exists():
        app.router.add_static("/static", STATIC_DIR)
    return app


app = create_app()

if __name__ == "__main__":
    logger.info("Starting MathRex Live server on http://%s:%s", HOST, PORT)
    web.run_app(app, host=HOST, port=PORT)
