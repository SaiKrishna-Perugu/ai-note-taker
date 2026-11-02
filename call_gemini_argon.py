import os
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

# 1. Load configuration from .env
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("LOCATION", "global")
MODEL_ID = os.getenv("MODEL_ID", "gemini-4-argon")

if not API_KEY and not PROJECT_ID:
    print("Error: Please set GEMINI_API_KEY or PROJECT_ID in your .env file.", file=sys.stderr)
    sys.exit(1)


LLM_PROVIDER = os.getenv("ALM_LLM_PROVIDER", os.getenv("AUTH_MODE", "vertex_express")).lower()


def create_gemini_client() -> genai.Client:
    """Initialize the Google GenAI Client.
    
    Supports:
    - vertex_express: Vertex AI in Express Mode with API Key (genai.Client(vertexai=True, api_key=...))
    - aistudio: Google AI Studio Developer API (genai.Client(api_key=...))
    - vertex_adc: Google Cloud Vertex AI via Application Default Credentials (IAM/ADC)
    """
    if LLM_PROVIDER in ("vertex_express", "vertex-express", "enterprise"):
        # Express mode authenticates with the API key on the global endpoint.
        # Project and location must be omitted; sending them routes the key
        # to a regional host that rejects express keys.
        if not API_KEY:
            raise ValueError("GEMINI_API_KEY is required for vertex_express.")
        return genai.Client(vertexai=True, api_key=API_KEY)

    elif LLM_PROVIDER in ("aistudio", "ai_studio", "gemini"):
        # Standard Google AI Studio API
        return genai.Client(api_key=API_KEY)

    elif LLM_PROVIDER in ("vertex_adc", "vertex", "vertex_iam"):
        # Vertex AI with Application Default Credentials (ADC)
        return genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION,
        )

    else:
        # Unknown provider: use the global express endpoint when a key exists,
        # otherwise Application Default Credentials.
        if API_KEY:
            return genai.Client(vertexai=True, api_key=API_KEY)
        return genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=LOCATION,
        )




def generate_text_response(client: genai.Client, prompt: str, model: str = MODEL_ID):
    """Generate a simple text response using Gemini 4 Argon."""
    print(f"Calling model '{model}' with prompt: \"{prompt}\"...\n")
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    return response.text


def generate_multimodal_response(
    client: genai.Client,
    image_uri: str = "gs://generativeai-downloads/images/scones.jpg",
    prompt: str = "What is shown in this image?",
    model: str = MODEL_ID,
):
    """Generate a multimodal response using a Cloud Storage image URI."""
    print(f"Calling model '{model}' with image '{image_uri}'...\n")
    response = client.models.generate_content(
        model=model,
        contents=[
            prompt,
            types.Part.from_uri(
                file_uri=image_uri,
                mime_type="image/jpeg",
            ),
        ],
    )
    return response.text


def main():
    try:
        client = create_gemini_client()
        
        # Simple text prompt test
        prompt = "Explain in two sentences what Gemini 4 Argon is specialized for."
        result = generate_text_response(client, prompt)
        print("=== Text Response ===")
        print(result)
        print()

        # Multimodal test using Google Cloud Storage image
        # print("=== Multimodal Response ===")
        # mm_result = generate_multimodal_response(client)
        # print(mm_result)

    except APIError as e:
        print(f"\n[API Error {e.code}]: {e.message}", file=sys.stderr)
        if e.code == 401:
            print("\nHint: Authentication failed. Verify that your API key is valid and has permissions for Vertex AI / Enterprise services.")
        elif e.code == 402:
            print("\nHint: Prepayment credits are depleted. Manage your billing at https://ai.studio/projects or enable billing on your Google Cloud project.")
        elif e.code == 404:
            print(f"\nHint: Model '{MODEL_ID}' was not found in the region '{LOCATION}'. Ensure your project has access to this model.")
    except Exception as ex:
        print(f"\n[Unexpected Error]: {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
