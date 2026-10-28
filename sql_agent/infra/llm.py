"""Explicit model, endpoint, credentials and transport retries."""
class AgentModel:
    def __init__(self, settings):
        from langchain_openai import ChatOpenAI
        if not settings.llm_model or not settings.llm_api_key.get_secret_value():
            raise ValueError('Заполните LLM_MODEL и LLM_API_KEY в .env.agent; endpoint укажите в LLM_BASE_URL.')
        options = dict(model=settings.llm_model, api_key=settings.llm_api_key.get_secret_value(),
                       base_url=settings.llm_base_url or None, timeout=settings.llm_timeout,
                       max_tokens=settings.llm_max_tokens, max_retries=1,
                       use_responses_api=settings.llm_responses)
        if settings.llm_responses:
            options['reasoning'] = {'effort': settings.llm_reasoning_effort}
        self.client = ChatOpenAI(**options)

    def invoke(self, messages, tools):
        return self.client.bind_tools(tools, parallel_tool_calls=False).invoke(messages)
