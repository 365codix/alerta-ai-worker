import asyncio
import os
import requests
import logging
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, JobProcess
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import openai, silero

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
    # La IA solo debe unirse a salas de emergencias ("vigia_...")
    if not ctx.room.name.startswith("vigia_"):
        logger.info(f"Ignorando sala {ctx.room.name} (no es de vigia)")
        return
        
    logger.info(f"Conectando a sala de emergencia: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Extraer el ID o token de la llamada desde el nombre de la sala si es necesario
    # ej: vigia_45 -> 45
    llamada_id = ctx.room.name.replace("vigia_", "")

    # Configurar el Agente con Pipeline Económico y Rápido
    # STT: OpenAI Whisper (transcripción)
    # LLM: GPT-4o-mini (Cerebro)
    # TTS: OpenAI TTS (Voz sintética)
    agent = VoicePipelineAgent(
        vad=silero.VAD.load(),
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        chat_ctx=openai.ChatContext().append(
            role="system",
            text=SYSTEM_PROMPT
        ),
    )

    agent.start(ctx.room)
    
    # Saludar al inicio de la llamada
    await agent.say("Central de Serenazgo, ¿cuál es su emergencia?", allow_interruptions=True)
    
    # Función que se ejecuta cuando el usuario se desconecta o la IA decide terminar
    @ctx.room.on("disconnected")
    def on_disconnected():
        logger.info("El ciudadano se ha desconectado o la sala se cerró.")
        # Opcional: Obtener historial de chat transcrito
        transcript = "\n".join([msg.text for msg in agent.chat_ctx.messages if msg.role in ["user", "assistant"]])
        
        # Enviar Webhook a Laravel (Tu servidor Hostinger)
        webhook_url = os.getenv("LARAVEL_WEBHOOK_URL")
        token = os.getenv("CALL_TOKEN", llamada_id) # O extraerlo del participant
        
        if webhook_url:
            try:
                # Se asume URL formato: https://alerta.civix.pe/inbox/central/vigia/api/llamada/{token}/webhook-ia
                # Ajusta la URL en tu panel según tu token/logica si es necesario
                # Por ahora enviamos a la raiz si la tienes configurada entera
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
        worker_type="job" # Puede ser 'job' o 'room' 
    ))
