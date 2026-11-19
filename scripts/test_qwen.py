
import os

from dotenv import load_dotenv
from ollama import Client

# Load model configuration
load_dotenv()

model = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")

client = Client(host=host, timeout=120.0)

print(f"Connecting to {model}...", flush=True)

try:
    response = client.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are part of Tor Lawyer, "
                    "a Bangladesh agreement review project. "
                    "Follow instructions directly. "
                    "Do not provide explanations."
                )
            },
            {
                "role": "user",
                "content": (
                    "Output exactly: "
                    "Tor Lawyer AI connected successfully."
                )
            }
        ],
        options={
            "temperature": 0,
            "num_predict": 100
        }
    )

    answer = response.message.content.strip()

    print("\nQwen response:")
    print(answer)

    if answer == "Tor Lawyer AI connected successfully.":
        print("\nSUCCESS: AI connection and response verified!")
    else:
        print("\nConnection works, but response differs.")

except Exception as error:
    print("\nERROR:", error)
