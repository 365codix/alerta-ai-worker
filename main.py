import asyncio
import os
import requests
import logging
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, AgentSession, Agent
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
    if not ctx.room.name.startswith("vigia_"):
        logger.info(f"Ignorando sala {ctx.room.name} (no es de vigia)")
        return
        
    logger.info(f"Conectando a sala de emergencia: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    llamada_id = ctx.room.name.replace("vigia_", "")

    # Configurar el Agente con la nueva API AgentSession (v1.5+)
    agent = Agent(
        instructions=SYSTEM_PROMPT
    )

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
    )

    await session.start(room=ctx.room, agent=agent)
    
    # Saludar al inicio de la llamada
    # En la nueva versión, podemos usar el chat ctx o simplemente un evento cuando termine de conectar
    await asyncio.sleep(1) # breve pausa para asegurar conexión de audio
    
    # Función que se ejecuta cuando el usuario se desconecta o la IA decide terminar
    @ctx.room.on("disconnected")
    def on_disconnected():
        logger.info("El ciudadano se ha desconectado o la sala se cerró.")
        # Opcional: Obtener historial de chat transcrito
        transcript = "\n".join([msg.text for msg in agent.chat_ctx.messages if msg.role in ["user", "assistant"]])
        
        webhook_url = os.getenv("LARAVEL_WEBHOOK_URL")
        token = os.getenv("CALL_TOKEN", llamada_id)
        
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
