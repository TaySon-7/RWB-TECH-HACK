import time

from fastapi.testclient import TestClient

from sql_agent.service import AgentService
from sql_agent.settings import Settings
from sql_agent.web import create_app
from sql_agent.tests.test_agent import FakeDB, ScriptedModel, call, successful_steps


def test_api_dialogue_clarification_and_assets(tmp_path):
    settings=Settings(runtime_dir=tmp_path,require_grafana_auth=False,llm_model='',llm_api_key='')
    model=ScriptedModel([call('ask_user',question='За какой год?',options=['2017','2018']),*successful_steps()])
    service=AgentService(settings,db=FakeDB(),model=model)
    with TestClient(create_app(settings,service)) as client:
        assert client.get('/').status_code==200
        assert client.get('/static/agent.js').status_code==200
        assert not client.get('/health').json()['llm_configured']
        assert client.post('/api/chat',json={'text':'Август'},headers={'origin':'http://wrong.example'}).status_code==403
        response=client.post('/api/chat',json={'text':'Август'})
        assert response.status_code==202
        cid=response.json()['conversation_id']
        def result():
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                state=client.get('/api/chat/'+cid).json()
                if state['status']!='running':return state
                time.sleep(.01)
            raise AssertionError('Worker did not finish')
        assert result()['status']=='waiting'
        assert client.post('/api/chat',json={'text':'2018','conversation_id':cid,'resume':True}).status_code==202
        state=result()
        assert state['status']=='done' and state['result']['queries'][0]['rows']==[[10]]
        assert client.get('/api/chat/not-a-uuid').status_code==404
