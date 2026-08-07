"""Qwen3-8B QLoRA 微调 · RTX 4090D 24GB · CUDA 13.0"""
# ====== 第1格: 装依赖 ======
!pip install unsloth transformers datasets trl -q

# ====== 第2格: 上传 finetune_data.json 到当前目录 ======

# ====== 第3格: 训练 ======
import json, torch
from datasets import Dataset
from unsloth import FastLanguageModel
from transformers import TrainingArguments
from trl import SFTTrainer

# 加载模型
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3-8B-Instruct",
    max_seq_length=2048,
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model, r=16, target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    lora_alpha=16, use_gradient_checkpointing=True,
)

# 加载数据
with open("finetune_data.json", "r", encoding="utf-8") as f:
    data = [json.loads(l) for l in f]

dataset = Dataset.from_list([{
    "text": f"<|im_start|>system\n{d['instruction']}<|im_end|>\n<|im_start|>user\n{d['input']}<|im_end|>\n<|im_start|>assistant\n{d['output']}<|im_end|>"
} for d in data])
print(f"数据: {len(data)} 条")

# 训练
trainer = SFTTrainer(
    model=model, tokenizer=tokenizer, train_dataset=dataset,
    args=TrainingArguments(
        per_device_train_batch_size=2, gradient_accumulation_steps=4,
        num_train_epochs=3, learning_rate=2e-4,
        fp16=True, logging_steps=10, output_dir="./output",
        save_strategy="epoch",
    ),
)
trainer.train()
print("训练完成!")

# ====== 第4格: 保存 ======
model.save_pretrained("./lora_adapter")
tokenizer.save_pretrained("./lora_adapter")
print("LoRA 权重已保存到 ./lora_adapter")
