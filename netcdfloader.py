import random
import torch
import h5py
from glob import glob
import torch.utils.data as data


class NetCDFLoader(torch.utils.data.Dataset):
    def __init__(self, img_root, mask_root, split, data_type, prev_next):
        super(NetCDFLoader, self).__init__()

        self.split = split
        self.data_type = data_type
        self.prev_next = prev_next

        if split == 'train':
            self.paths = glob('{:s}/data_large/*.h5'.format(img_root),
                              recursive=True)
        elif split == 'test' or split == 'infill':
            self.paths = glob('{:s}/test_large/*.h5'.format(img_root))
        elif split == 'val':
            self.paths = glob('{:s}/val_large/*.h5'.format(img_root))
        self.maskpath = mask_root

        # define length of image and mask
        img_file = h5py.File('{:s}'.format(self.paths[0]), 'r')
        img_data = img_file.get(self.data_type)
        self.img_length = len(img_data[:, 1, 1])
        mask_file = h5py.File('{:s}'.format(self.maskpath), 'r')
        mask_data = mask_file.get(self.data_type)
        self.mask_length = len((mask_data[:, 1, 1]))

    def __len__(self):
        return self.img_length


class PrevNextImageNetCDFLoader(NetCDFLoader):
    def __getitem__(self, index):
        # open netcdf file for img and mask
        img_file = h5py.File('{:s}'.format(self.paths[0]), 'r')
        img_data = img_file.get(self.data_type)
        mask_file = h5py.File(self.maskpath)
        mask_data = mask_file.get(self.data_type)

        # get img and mask from index
        img_current = img_data[index, :, :]
        img_current = torch.from_numpy(img_current[:, :])
        img_current = img_current.unsqueeze(0)
        if self.split == 'infill':
            mask_current = mask_data[index, :, :]
        else:
            mask_current = mask_data[random.randint(0, self.mask_length - 1), :, :]
        mask_current = torch.from_numpy(mask_current[:, :])
        mask_current = mask_current.unsqueeze(0)
        # get images previous and next to image
        img_total = []
        img_total.append(img_current)
        mask_total = []
        mask_total.append(mask_current)
        for i in range(1, self.prev_next+1):
            # img
            prev_index = index-i
            if prev_index < 0:
                prev_index = 0
            next_index = index+i
            if next_index > self.img_length-1:
                next_index = self.img_length-1
            # get prev img
            img_prev = img_data[prev_index, :, :]
            img_prev = torch.from_numpy(img_prev[:, :])
            img_prev = img_prev.unsqueeze(0)
            img_total.insert(0, img_prev)
            # get next img
            img_next = img_data[next_index, :, :]
            img_next = torch.from_numpy(img_next[:, :])
            img_next = img_next.unsqueeze(0)
            img_total.append(img_next)

            # mask
            next_index = index+i
            if next_index > self.mask_length-1:
                next_index = self.mask_length-1
            # get prev mask
            if self.split == 'infill':
                mask_prev = mask_data[prev_index, :, :]
            else:
                mask_prev = mask_data[random.randint(0, self.mask_length - 1), :, :]
            mask_prev = torch.from_numpy(mask_prev[:, :])
            mask_prev = mask_prev.unsqueeze(0)
            # get next mask
            if self.split == 'infill':
                mask_next = mask_data[next_index, :, :]
            else:
                mask_next = mask_data[random.randint(0, self.mask_length - 1), :, :]
            mask_next = torch.from_numpy(mask_next[:, :])
            mask_next = mask_next.unsqueeze(0)
            mask_total.insert(0, mask_prev)
            mask_total.append(mask_next)
        img_total = torch.cat(img_total)
        mask_total = torch.cat(mask_total)

        return img_total * mask_total, mask_total, img_total