import asyncio
import os
import requests
import logging
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import openai, silero, deepgram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vigia-agent")

# Instrucciones del sistema para el comportamiento de la IA
SYSTEM_PROMPT = """
Eres un despachador de emergencias de Serenazgo (Seguridad Ciudadana). 
Tu trabajo es hacer un pre-triaje MUY BREVE a las personas que llaman reportando una emergencia.
Debes preguntar qué sucede y dónde están exactamente. 
Mantén tus respuestas cortas (1-2 oraciones máximo), calmadas y directas. 
Una vez tengas la naturaleza de la emergencia y la ubicación aproximada, infórmale al ciudadano que enviarás ayuda y finaliza tu atención diciendo que transferirás la llamada a un operador humano o unidad en camino.
Ejemplo de inicio: "Central de Serenazgo, ¿cuál es su emergencia?"
"""

async def entrypoint(ctx: JobContext):
    if not ctx.room.name.startswith("vigia_"):
        logger.info(f"Ignorando sala {ctx.room.name} (no es de vigia)")
        return
        
    llamada_token = ctx.room.name.replace("vigia_", "")
    webhook_url = os.getenv("LARAVEL_WEBHOOK_URL")

    # 1. Obtener el prompt dinámico desde Laravel
    system_prompt = SYSTEM_PROMPT # Fallback por si falla
    if webhook_url:
        try:
            # Reemplazar webhook-ia con prompt en la URL base (asumiendo que webhook_url incluye /api/llamada)
            prompt_url = f"{webhook_url}/{llamada_token}/prompt"
            response = requests.get(prompt_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("success") and data.get("prompt"):
                    system_prompt = data["prompt"]
                    logger.info("Prompt dinámico obtenido exitosamente desde Laravel.")
        except Exception as e:
            logger.error(f"Error obteniendo prompt desde Laravel: {e}. Usando prompt por defecto.")
        
    logger.info(f"Conectando a sala de emergencia: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    llamada_id = ctx.room.name.replace("vigia_", "")

    # Configurar el Agente usando VoicePipelineAgent (nueva API) con Deepgram para ultra baja latencia
    agent = VoicePipelineAgent(
        vad=silero.VAD.load(),
        stt=deepgram.STT(), # Aquí está la magia del streaming rápido
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        chat_ctx=None,
    )

    agent.start(ctx.room)
    
    # Saludar al inicio
    await asyncio.sleep(1)
    # Reemplazamos chat_ctx por agent.chat_ctx si queremos inyectar un system prompt local
    from livekit.agents.llm import ChatContext, ChatMessage
    chat_ctx = ChatContext()
    chat_ctx.messages.append(ChatMessage(role="system", content=system_prompt))
    agent.chat_ctx = chat_ctx
    
    # Función que se ejecuta cuando el usuario se desconecta o la IA decide terminar
    @ctx.room.on("disconnected")
    def on_disconnected():
        logger.info("El ciudadano se ha desconectado o la sala se cerró.")
        # Opcional: Obtener historial de chat transcrito
        transcript = "\n".join([msg.content for msg in agent.chat_ctx.messages if msg.role in ["user", "assistant"]])
        
        webhook_url = os.getenv("LARAVEL_WEBHOOK_URL")
        token = os.getenv("CALL_TOKEN", llamada_token)
        
        if webhook_url:
            try:
                final_url = f"{webhook_url}/{token}/webhook-ia" 
                response = requests.post(
                    final_url,
                    json={"transcripcion": transcript},
                    headers={"Accept": "application/json"}
                )
                logger.info(f"Webhook enviado: HTTP {response.status_code}")
            except Exception as e:
                logger.error(f"Error enviando webhook: {e}")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
    ))
