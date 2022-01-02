import torch
from torch import nn
import torch.nn.functional as F
import sys

sys.path.append('./')
from model.encoder_decoder import EncoderBlock
import config as cfg


class ConvolutionalAttentionBlock(nn.Module):
    def __init__(self, img_sizes=[512], enc_layers=[4], pool_layers=[4], in_channels=[1],
                 lstm=True):
        super().__init__()

        assert len(img_sizes) == len(enc_layers) == len(pool_layers) == len(in_channels)

        # define channel attention blocks
        self.channel_attention_blocks = []
        for i in range(len(img_sizes)):
            channel_attention_block = {}
            encoding_layers = []
            encoding_layers.append(
                EncoderBlock(
                    in_channels=in_channels[i],
                    out_channels=img_sizes[i] // (2 ** (enc_layers[i] - 1)),
                    image_size=img_sizes[i],
                    kernel=(7, 7), stride=(2, 2), activation=nn.ReLU(), lstm=lstm))
            for j in range(1, enc_layers[i]):
                if i == enc_layers[i] - 1:
                    encoding_layers.append(EncoderBlock(
                        in_channels=img_sizes[i] // (2 ** (enc_layers[i] - j)),
                        out_channels=img_sizes[i] // (2 ** (enc_layers[i] - j - 1)),
                        image_size=img_sizes[i] // (2 ** j),
                        kernel=(3, 3), stride=(2, 2), activation=nn.ReLU(), lstm=lstm))
                else:
                    encoding_layers.append(EncoderBlock(
                        in_channels=img_sizes[i] // (2 ** (enc_layers[i] - j)),
                        out_channels=img_sizes[i] // (2 ** (enc_layers[i] - j - 1)),
                        image_size=img_sizes[i] // (2 ** j),
                        kernel=(5, 5), stride=(2, 2), activation=nn.ReLU(), lstm=lstm))
            # define encoding pooling layers
            for j in range(pool_layers[i]):
                encoding_layers.append(EncoderBlock(
                    in_channels=img_sizes[i],
                    out_channels=img_sizes[i],
                    image_size=img_sizes[i] // (2 ** (enc_layers[i] + j)),
                    kernel=(3, 3), stride=(2, 2), activation=nn.ReLU(), lstm=lstm))
            channel_attention_block['encoding_layers'] = nn.ModuleList(encoding_layers).to(cfg.device)
            channel_attention_block['mlp'] = nn.Sequential(
                nn.Conv2d(in_channels=img_sizes[i], out_channels=img_sizes[i] // cfg.channel_reduction_rate,
                          kernel_size=(1, 1)),
                nn.ReLU(),
                nn.Conv2d(in_channels=img_sizes[i] // cfg.channel_reduction_rate, out_channels=img_sizes[i],
                          kernel_size=(1, 1)),
            ).to(cfg.device)
            self.channel_attention_blocks.append(channel_attention_block)

        # define spatial attention block
        self.spatial_attention_block = nn.Sequential(
            nn.Conv2d(in_channels=2, out_channels=1, kernel_size=(3, 3), padding=(1, 1)),
            nn.Sigmoid()
        )

    def forward(self, rea_input, rea_mask, h, h_mask):
        # calculate channel attention
        h_channel_attentions = []
        for i in range(len(self.channel_attention_blocks)):
            h_channel_attention = self.forward_channel_attention(
                self.channel_attention_blocks[i], rea_input[:, i, :, :, :, :], rea_mask[:, i, :, :, :, :])
            h_channel_attentions.append(h_channel_attention * h)

        # calculate spatial attention
        h_spatial_attention = torch.unsqueeze(self.spatial_attention_block(
            torch.cat([torch.max(h[:, 0, :, :, :], keepdim=True, dim=1)[0],
                       torch.mean(h[:, 0, :, :, :], keepdim=True, dim=1)], dim=1)
        ), dim=1)

        h_total_attentions = []
        for i in range(len(h_channel_attentions)):
            h_total_attention = h_channel_attentions[i] * h_spatial_attention
            h_total_attentions.append(h_total_attention)

        h_total_attentions = torch.cat(h_total_attentions, dim=2)
        h_mask_total_attentions = torch.ones_like(h_total_attentions)
        h = torch.cat([h, h_total_attentions], dim=2)
        h_mask = torch.cat([h_mask, h_mask_total_attentions], dim=2)

        return h, h_mask

    def forward_channel_attention(self, attention_block, input, input_mask):
        for i in range(len(attention_block['encoding_layers'])):
            input, input_mask, _ = attention_block['encoding_layers'][i](input,
                                                                         input_mask,
                                                                         None)
        fusion_max = F.max_pool2d(input[:, 0, :, :, :], input.shape[3])
        fusion_avg = F.avg_pool2d(input[:, 0, :, :, :], input.shape[3])

        fusion_attention = torch.sigmoid(
            attention_block['mlp'](fusion_max) + attention_block['mlp'](fusion_avg)
        )

        return input * torch.unsqueeze(fusion_attention, dim=1)