"""LoRA 推理服务 · 4080 16GB · 4bit GPU"""
import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel
import uvicorn

app = FastAPI()

BASE = "./Qwen2.5-7B-Instruct"
LORA = "./lora_adapter"

print("Loading 4bit model...")
bnb = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)
base = AutoModelForCausalLM.from_pretrained(
    BASE, quantization_config=bnb, device_map="auto",
    trust_remote_code=True
)
model = PeftModel.from_pretrained(base, LORA, device_map="auto")
model.eval()
tokenizer = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
print("Model ready on GPU!")

class PromptRequest(BaseModel):
    prompt: str
    max_tokens: int = 512

@app.post("/generate")
async def generate(req: PromptRequest):
    inputs = tokenizer(req.prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        outputs = model.generate(**inputs, max_new_tokens=req.max_tokens, temperature=0, do_sample=False)
    text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    return {"text": text.split("assistant")[-1].strip() if "assistant" in text else text}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)
