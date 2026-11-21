import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class ResearchResponse(BaseModel):
    topic: str = Field(description="Short title of the research topic, e.g. 'Sharks'")
    summary: str = Field(description="The research summary only. Do not include topic, tools or sources here.")
    sources: list[str] = Field(description="URLs or titles of the sources used")

llm = OpenAI(base_url="https://llm-hackathon.sherpaswealth.com/v1", api_key=os.environ["TEAM_KEY"])

system_prompt = """
You write short financial education pills for a family, on behalf of their advisor.
Rules:
- Use ONLY the facts and numbers given in the context. Never invent figures.
- Match the person's risk profile and what they already know. Do not repeat what they have seen.
- Calm, warm tone. If nothing needs action, say so.
- Never recommend buying or selling specific securities.
"""

agent = create_agent(
    model=llm,
    tools=tools,
    system_prompt=system_prompt,
    response_format=ToolStrategy(ResearchResponse),
)

query = input("Enter your research query: ")

reply = llm.chat.completions.create(model="gpt-5-mini", messages=[{"role": "user", "content": "Hola, ¿cómo estás?"}])
print(reply.choices[0].message.content)
