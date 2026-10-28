"""A small same-origin API; Grafana remains the dashboard and login provider."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .service import AgentService
from .settings import Settings

STATIC = Path(__file__).with_name('static')


class ChatRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    conversation_id: str | None = None
    resume: bool = False


def create_app(settings=None, service=None):
    settings = settings or Settings()
    service = service or AgentService(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title='Capibara · SQL agent', lifespan=lifespan)
    app.state.service = service

    async def owner(request: Request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'Запрос должен приходить из этого интерфейса.')
        if not settings.require_grafana_auth:
            return 'local'
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(settings.grafana_url + '/api/user',
                                            headers={'Cookie': request.headers.get('cookie', '')})
        except httpx.TransportError:
            raise HTTPException(503, 'Grafana пока недоступна. Повторите через несколько секунд.') from None
        if response.status_code != 200:
            raise HTTPException(401, 'Сначала войдите в Grafana, затем откройте агента.')
        return 'grafana:' + str(response.json()['id'])

    @app.get('/health')
    def health():
        return {'status': 'ok', 'backend': settings.backend,
                'llm_configured': bool(settings.llm_model and settings.llm_api_key.get_secret_value())}

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html')

    @app.post('/api/chat', status_code=202)
    def chat(body: ChatRequest, user=Depends(owner)):
        try:
            text = body.text.strip()
            if not text:
                raise ValueError('Напишите вопрос.')
            conversation_id = service.submit(text, user, body.conversation_id, resume=body.resume)
            return {'conversation_id': conversation_id}
        except KeyError:
            raise HTTPException(404, 'Диалог не найден.') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get('/api/chat/{conversation_id}')
    def status(conversation_id: str, user=Depends(owner)):
        try:
            return service.read(conversation_id, user)
        except (ValueError, KeyError):
            raise HTTPException(404, 'Диалог не найден.') from None

    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    return app


app = create_app()
