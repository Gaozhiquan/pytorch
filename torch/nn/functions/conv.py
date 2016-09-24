from torch.autograd import Function
from torch._thnn import type2backend
import torch.backends.cudnn as cudnn
import torch.backends.cudnn.conv


class Conv2d(Function):
    def __init__(self, stride, pad, groups):
        super(Conv2d, self).__init__()
        self.stride = stride
        self.pad = pad
        self.groups = groups

    def forward(self, input, weight, bias=None):
        output = input.new(*self._output_size(input, weight))
        if bias is not None:
            self.save_for_backward(input, weight, bias)
        else:
            self.save_for_backward(input, weight)

        if cudnn.is_acceptable(input):
            cudnn.conv.forward(self, input, weight, bias, output)
        else:
            # TODO: implement groups for THNN
            if self.groups != 1:
                raise ValueError('THNN does not support groups')
            backend = type2backend[type(input)]
            self._finput = input.new()
            self._fgrad_input = input.new()
            backend.SpatialConvolutionMM_updateOutput(
                backend.library_state, input, output, weight, bias,
                self._finput, self._fgrad_input, weight.size(3), weight.size(2),
                self.stride[1], self.stride[0], self.pad[1], self.pad[0])

        return output

    def backward(self, grad_output):
        tensors = self.saved_tensors
        if len(tensors) == 2:
            input, weight = tensors
            bias = None
        else:
            input, weight, bias = tensors

        grad_input, grad_weight, grad_bias = None, None, None

        if cudnn.is_acceptable(input):
            if self.needs_input_grad[0]:
                grad_input = cudnn.conv.backward_data(
                    self, grad_output, input, weight)

            if self.needs_input_grad[1]:
                grad_weight = cudnn.conv.backward_filter(
                    self, grad_output, input, weight)

            if bias is not None and self.needs_input_grad[2]:
                grad_bias = cudnn.conv.backward_bias(
                    self, grad_output, bias)
        else:
            backend = type2backend[type(input)]
            if self.needs_input_grad[0]:
                grad_input = input.new().resize_as_(input).zero_()
                backend.SpatialConvolutionMM_updateGradInput(
                    backend.library_state, input, grad_output, grad_input,
                    weight, self._finput, self._fgrad_input, weight.size(3),
                    weight.size(2), self.stride[1], self.stride[0], self.pad[1],
                    self.pad[0])

            if any(self.needs_input_grad[1:]):
                grad_weight = weight.new().resize_as_(weight).zero_()
                if bias is not None and self.needs_input_grad[2]:
                    grad_bias = bias.new().resize_as_(bias).zero_()
                else:
                    grad_bias = None
                backend.SpatialConvolutionMM_accGradParameters(
                    backend.library_state, input, grad_output, grad_weight,
                    grad_bias, self._finput, self._fgrad_input, weight.size(3),
                    weight.size(2), self.stride[1], self.stride[0], self.pad[1],
                    self.pad[0], 1)

        if bias is not None:
            return grad_input, grad_weight, grad_bias
        else:
            return grad_input, grad_weight

    def _output_size(self, input, weight):
        kh, kw = weight.size(2), weight.size(3)
        h = (input.size(2) + 2 * self.pad[0] - kh) // self.stride[0] + 1
        w = (input.size(3) + 2 * self.pad[1] - kw) // self.stride[1] + 1

        return input.size(0), weight.size(0), h, w