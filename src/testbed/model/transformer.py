import math
import torch
from torch.nn import Module, Dropout, Linear
from .nn import Sequential, CrossEntropyLoss, Softmax, Embedding


class ResidualDropoutLayerNorm(Module):
    def __init__(self, layer, d_model, p_dropout):
        super().__init__()
        self.d_model = d_model
        self.p_dropout = p_dropout

        self.layer = layer
        self.dropout = Dropout(p_dropout)
        self.layernorm = LayerNorm(d_model)

    def forward(self, x):
        assert x.shape[-1] == self.d_model, f"{x.shape[-1]} != {self.d_model}"
        return self.layernorm(x+self.dropout(self.layer(x)))


class Mask(Module):
    def __init__(self, mode="half_causal"):
        super().__init__()
        self.mode = mode

    def forward(self, x):
        n, device = x.shape[-1], x.device
        return x+(1-1/torch.cat([
            torch.cat([
                torch.ones((n//2,n//2),device=device),
                torch.zeros((n//2,n//2),device=device)], dim=1),
            torch.tril(torch.ones((n,n),device=device))[n//2:,:]], dim=0))


class Attn(Module):
    def __init__(self, d_model, d_k, d_v, n_heads, p_dropout):
        super().__init__()
        self.d_model = d_model
        self.d_k = d_k
        self.d_v = d_v
        self.n_heads = n_heads
        self.p_dropout = p_dropout

        self.query_proj = Linear(d_model, d_k*n_heads)
        self.key_proj = Linear(d_model, d_k*n_heads)
        self.value_proj = Linear(d_model, d_v*n_heads)
        self.mask = Mask()
        self.dropout = Dropout(p_dropout)
        self.softmax = torch.nn.Softmax(dim=-1)
        self.linear = Linear(d_v*n_heads, d_model, bias=False)

    def forward(self, x):
        (n_ctx, d_model) = x.shape[-2:]
        assert d_model == self.d_model, f"{d_model} != {self.d_model}"
        split_heads = lambda x: x.view(x.shape[:-1]+(self.n_heads,-1)).transpose(-2,-3).contiguous()
        merge_heads = lambda x: x.transpose(-2,-3).contiguous().view(x.shape[:-3]+(n_ctx,self.d_v*self.n_heads))
        (Q, K, V) = map(split_heads,(self.query_proj(x),self.key_proj(x),self.value_proj(x)))
        QKT = torch.matmul(Q/math.sqrt(self.d_k),K.transpose(-1,-2))
        return self.linear(merge_heads(self.dropout(self.softmax(self.mask(QKT)))@V))


class TransformerLayer(Module):
    def __init__(self, d_model, d_k, d_v, n_heads, d_ff, p_dropout_attn_mat, p_dropout_attn_out, p_dropout_ff):
        super().__init__()
        self.d_model = d_model
        self.d_k = d_k
        self.d_v = d_v
        self.n_heads = n_heads
        self.d_ff = d_ff
        self.p_dropout_attn_mat = p_dropout_attn_mat
        self.p_dropout_attn_out = p_dropout_attn_out
        self.p_dropout_ff = p_dropout_ff

        self.attn = ResidualDropoutLayerNorm(Attn(d_model, d_k, d_v, n_heads, p_dropout_attn_mat), d_model, p_dropout_attn_out)
        self.ff = ResidualDropoutLayerNorm(MLP(d_model, d_ff), d_model, p_dropout_ff)

    def forward(self, x):
        return self.ff(self.attn(x))


class PositionalEncoding(Module):
    def __init__(self, max_ctx, d_model):
        super().__init__()
        self.max_ctx = max_ctx
        self.d_model = d_model
        self.weight = torch.nn.Parameter(0.02*torch.randn(max_ctx, d_model))

    def forward(self, x):
        n_ctx = x.shape[-2]
        return x + self.weight[-n_ctx:]


class Transformer(Module):
    def __init__(self,
                 n_vocab_in=256,
                 n_vocab_out=256,
                 max_ctx=128,
                 d_model=1024,
                 d_k=32,
                 d_v=32,
                 n_heads=32,
                 d_ff=8192,
                 n_layers=12,
                 p_dropout_embedding=0.1,
                 p_dropout_attn_mat=0.1,
                 p_dropout_attn_out=0.1,
                 p_dropout_ff=0.1,
                 use_amp=False):
        super().__init__()
        self.n_vocab_in = n_vocab_in
        self.n_vocab_out = n_vocab_out
        self.max_ctx = max_ctx
        self.d_model = d_model
        self.d_k = d_k
        self.d_v = d_v
        self.n_heads = n_heads
        self.d_ff = d_ff
        self.n_layers = n_layers
        self.p_dropout_embedding = p_dropout_embedding
        self.p_dropout_attn_mat = p_dropout_attn_mat
        self.p_dropout_attn_out = p_dropout_attn_out
        self.p_dropout_ff = p_dropout_ff

        self.module = (
            Sequential(
                Embedding(n_vocab_in, d_model),
                Dropout(p_dropout_embedding),
                PositionalEncoding(max_ctx,d_model),
                Sequential(
                    TransformerLayer(d_model, d_k, d_v, n_heads, d_ff,
                                     p_dropout_attn_mat, p_dropout_attn_out, p_dropout_ff)
                    for _ in range(n_layers)))
                Linear(d_model, n_vocan_out))
        self.softmax = Softmax()
        self.criterion = CrossEntropyLoss()

    def forward(self, X):
        try:
            (x, y) = split_example(xy)
            return self.crossentropyloss(self.module(x), y)
        except:
            return self.softmax(self.module(x))


            if not probs:
                Y = X[...,1:].contiguous()
                X = X[...,:-1].contiguous()
            n_ctx = X.shape[-1]
            assert n_ctx <= self.max_ctx
            X = self.decoder(self.encoder(X))
            if probs:
                return self.softmax(X)
            else:
                return self.criterion(X[...,n_ctx//2:,:].reshape(-1,self.n_vocab_out),
                                      Y[...,n_ctx//2:].reshape(-1)).view(X.shape[:-2]+(-1,))/math.log(self.n_vocab_out)

    def probs(self, X):
        with torch.no_grad():
            return self.forward(X, probs=True)
