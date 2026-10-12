import pytest

from infinidojo.openwam_service import encode_on_encoder_device, move_with_cpu_text

torch = pytest.importorskip("torch")


def test_encoder_never_moves_with_backbone_and_registration_survives_failure():
    backbone = torch.nn.Module()
    backbone.text_encoder = torch.nn.Linear(2, 2)
    backbone.dit = torch.nn.Linear(2, 2)
    encoder = backbone.text_encoder

    def original(module, dtype, device):
        assert module.text_encoder is None
        module.to(dtype=dtype, device=device)

    move_with_cpu_text(backbone, torch.bfloat16, torch.device("meta"), original)
    assert backbone.dit.weight.device.type == "meta"
    assert backbone.text_encoder is encoder
    assert encoder.weight.device.type == "cpu" and encoder.weight.dtype == torch.bfloat16
    assert {name for name, _ in backbone.named_parameters()} == {
        "text_encoder.weight",
        "text_encoder.bias",
        "dit.weight",
        "dit.bias",
    }

    def failing(*args):
        raise RuntimeError("device allocation failed")

    with pytest.raises(RuntimeError, match="allocation failed"):
        move_with_cpu_text(backbone, torch.bfloat16, torch.device("meta"), failing)
    assert backbone.text_encoder is encoder


def test_text_encoding_uses_encoder_device_then_returns_to_requested_device():
    encoder = torch.nn.Linear(2, 2)
    context = torch.tensor([[1.0, 2.0]])
    lengths = torch.tensor([2])
    calls = []

    def original(prompts, *, tokenizer, text_encoder, device):
        calls.append((prompts, tokenizer, text_encoder, device))
        assert device == encoder.weight.device
        return context, lengths

    result = encode_on_encoder_device(
        original, ["stack"], tokenizer="tokenizer", text_encoder=encoder, device="cpu"
    )
    torch.testing.assert_close(result[0], context)
    torch.testing.assert_close(result[1], lengths)
    result = encode_on_encoder_device(
        original, ["stack"], tokenizer="tokenizer", text_encoder=encoder, device="meta"
    )
    assert all(t.device.type == "meta" for t in result)
    assert len(calls) == 2
