import torch
import torch.nn as nn

from datasets import load_dataset
import functools
from collections import defaultdict

from functools import partial
import numpy as np
from tqdm import tqdm
from autosmoothquant.models import _MODEL_TYPE


def _model_preprocess(model):
    architectures = getattr(model.config, "architectures", [])
    model_type = _MODEL_TYPE[architectures[0]]
    info_dict = {"model_type": model_type}
    if model_type == "mixtral":
        original_top_k = model.model.layers[0].block_sparse_moe.top_k
        num_local_experts = getattr(model.config, "num_local_experts")
        info_dict["original_top_k"] = original_top_k
        # To get all expert act scales, we set top_k to the number of total experts here.
        for layer in model.model.layers:
            layer.block_sparse_moe.top_k = num_local_experts
    return info_dict

def _model_postprocess(model, info_dict):
    if info_dict["model_type"] == "mixtral":
        original_top_k = info_dict["original_top_k"]
        # Reset top_k to original top_k
        for layer in model.model.layers:
            layer.block_sparse_moe.top_k = original_top_k

def get_act_scales(model, tokenizer, dataset_path, num_samples=512, seq_len=512):
    model.eval()
    info_dict = _model_preprocess(model)
    device = next(model.parameters()).device
    act_scales = {}

    def stat_tensor(name, tensor):
        hidden_dim = tensor.shape[-1]
        tensor = tensor.view(-1, hidden_dim).abs().detach()
        comming_max = torch.max(tensor, dim=0)[0].float().cpu()
        if name in act_scales:
            act_scales[name] = torch.max(act_scales[name], comming_max)
        else:
            act_scales[name] = comming_max

    def stat_input_hook(m, x, y, name):
        if isinstance(x, tuple):
            x = x[0]
        stat_tensor(name, x)

    hooks = []
    for name, m in model.named_modules():
        if isinstance(m, nn.Linear):
            hooks.append(
                m.register_forward_hook(
                    functools.partial(stat_input_hook, name=name))
            )

    dataset = load_dataset("json", data_files=dataset_path, split="train")
    dataset = dataset.shuffle(seed=42)

    for i in tqdm(range(num_samples)):
        input_ids = tokenizer(dataset[i]["text"], return_tensors="pt",
                              max_length=seq_len, truncation=True).input_ids.to(device)
        model(input_ids)

    for h in hooks:
        h.remove()
        
    _model_postprocess(model, info_dict)

    return act_scales

@torch.no_grad()
def collect_transformers_layer_scales(model, act_dict):
    decoder_layer_scales = []
    for idx in range(model.config.num_hidden_layers):
        scale_dict = {}
        scale_dict["attn_input_scale"] = act_dict[
            f"model.decoder.layers.{idx}.self_attn.q_proj"]['input'] / 127
        scale_dict["q_output_scale"] = act_dict[
            f"model.decoder.layers.{idx}.self_attn.q_proj"]['output'] / 127
        scale_dict["k_output_scale"] = act_dict[
            f"model.decoder.layers.{idx}.self_attn.k_proj"]['output'] / 127
        scale_dict["v_output_scale"] = act_dict[
            f"model.decoder.layers.{idx}.self_attn.v_proj"]['output'] / 127
        scale_dict["out_input_scale"] = act_dict[
            f"model.decoder.layers.{idx}.self_attn.out_proj"]['input'] / 127
        scale_dict["fc1_input_scale"] = act_dict[
            f"model.decoder.layers.{idx}.fc1"]['input'] / 127
        scale_dict["fc2_input_scale"] = act_dict[
            f"model.decoder.layers.{idx}.fc2"]["input"] / 127
        decoder_layer_scales.append(scale_dict)

    return decoder_layer_scales


@torch.no_grad()
def collect_llama_layer_scales(model, act_dict):
    decoder_layer_scales = []
    for idx in range(model.config.num_hidden_layers):
        scale_dict = {}
        scale_dict["attn_input_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.q_proj"]['input'] / 127
        scale_dict["q_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.q_proj"]['output'] / 127
        scale_dict["k_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.k_proj"]['output'] / 127
        scale_dict["v_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.v_proj"]['output'] / 127
        scale_dict["out_input_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.o_proj"]['input'] / 127
        # mlp scales
        scale_dict["gate_input_scale"] = act_dict[
            f"model.layers.{idx}.mlp.gate_proj"]['input'] / 127
        scale_dict["down_input_scale"] = act_dict[
            f"model.layers.{idx}.mlp.down_proj"]["input"] / 127
        decoder_layer_scales.append(scale_dict)

    return decoder_layer_scales

@torch.no_grad()
def collect_baichuan_layer_scales(model, act_dict):
    decoder_layer_scales = []
    for idx in range(model.config.num_hidden_layers):
        scale_dict = {}
        scale_dict["attn_input_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.W_pack"]['input'] / 127
        scale_dict["attn_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.W_pack"]['output'] / 127
        scale_dict["out_input_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.o_proj"]['input'] / 127
        # mlp scales
        scale_dict["gate_input_scale"] = act_dict[
            f"model.layers.{idx}.mlp.gate_proj"]['input'] / 127
        scale_dict["down_input_scale"] = act_dict[
            f"model.layers.{idx}.mlp.down_proj"]["input"] / 127
        decoder_layer_scales.append(scale_dict)

    return decoder_layer_scales

@torch.no_grad()
def collect_mixtral_layer_scales(model, act_dict):
    decoder_layer_scales = []
    for idx in range(model.config.num_hidden_layers):
        scale_dict = {}
        scale_dict["attn_input_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.q_proj"]['input'] / 127
        scale_dict["q_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.q_proj"]['output'] / 127
        scale_dict["k_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.k_proj"]['output'] / 127
        scale_dict["v_output_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.v_proj"]['output'] / 127
        scale_dict["out_input_scale"] = act_dict[
            f"model.layers.{idx}.self_attn.o_proj"]['input'] / 127
        # moe scales
        scale_dict["moe_input_scale"] = act_dict[
            f"model.layers.{idx}.block_sparse_moe.gate"]['input'] / 127
        down_input_scales = []
        num_local_experts = getattr(model.config, "num_local_experts")
        for i in range(num_local_experts):
            down_input_scales.append(act_dict[f"model.layers.{idx}.block_sparse_moe.experts.{i}.w2"]['input'] / 127)
        scale_dict["down_input_scales"] = down_input_scales
        decoder_layer_scales.append(scale_dict)

    return decoder_layer_scales

@torch.no_grad()
def get_static_decoder_layer_scales(model,
                                    tokenizer,
                                    dataset_path,
                                    num_samples=512,
                                    seq_len=512,
                                    model_type = "transformers"
                                    ):
    model.eval()
    device = next(model.parameters()).device
    act_dict = defaultdict(dict)

    def stat_io_hook(m, x, y, name):
        if isinstance(x, tuple):
            x = x[0]
        if name not in act_dict or "input" not in act_dict[name]:
            act_dict[name]["input"] = x.detach().abs().max().item()
        else:
            act_dict[name]["input"] = max(
                act_dict[name]["input"], x.detach().abs().max().item())
        if isinstance(y, tuple):
            y = y[0]
        if name not in act_dict or "output" not in act_dict[name]:
            act_dict[name]["output"] = y.detach().abs().max().item()
        else:
            act_dict[name]["output"] = max(
                act_dict[name]["output"], y.detach().abs().max().item())

    hooks = []
    for name, m in model.named_modules():
        if isinstance(m, torch.nn.Linear):
            hooks.append(m.register_forward_hook(
                partial(stat_io_hook, name=name)))

    print("Collecting activation scales...")
    pbar = tqdm(range(num_samples))
    dataset = load_dataset('json', data_files=dataset_path, split="train")
    dataset = dataset.shuffle(seed=42)
    for i in pbar:
        input_ids = tokenizer(dataset[i]["text"], return_tensors="pt",
                              max_length=seq_len, truncation=True).input_ids.to(device)
        model(input_ids)
        mean_scale = np.mean([v["input"] for v in act_dict.values()])
        pbar.set_description(f"Mean input scale: {mean_scale:.2f}")
    for hook in hooks:
        hook.remove()

    decoder_layer_scales = []
    if model_type == "transformers":
        decoder_layer_scales = collect_transformers_layer_scales(model, act_dict)
    elif model_type == "llama":
        decoder_layer_scales = collect_llama_layer_scales(model, act_dict)
    elif model_type == "baichuan":
        decoder_layer_scales = collect_baichuan_layer_scales(model, act_dict)
    elif model_type == "mixtral":
        decoder_layer_scales = collect_mixtral_layer_scales(model, act_dict)
    else:
        raise ValueError(f"unsupport model type: {model_type}")

    return decoder_layer_scales, act_dict
