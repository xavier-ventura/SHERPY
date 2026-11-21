import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

llm = OpenAI(base_url="https://llm-hackathon.sherpaswealth.com/v1", api_key=os.environ["TEAM_KEY"])
reply = llm.chat.completions.create(model="gpt-5-mini", messages=[{"role": "user", "content": "Explain a 401(k) in two sentences."}])
print(reply.choices[0].message.content)