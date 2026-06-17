import numpy as np
import os
import re
import sys
import math
import time
import io
import struct
import zlib
import fnmatch
from PIL import Image

'''
MIT License

Copyright (c) 2025 VincentNL

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

-----

PyPVR is a modern Python tool for encoding / decoding PowerVR2 images used by SEGA Naomi and SEGA Dreamcast.
All texture modes, pixel formats, palettes and PVR variations used by SEGA's SDK are supported.

--------
CREDITS
--------

 - Rob2d for K-means idea leading to quality VQ encoding
 - Egregiousguy for YUV420 decoding 
 - Kion for SmallVQ mipmaps data
 - MetalliC for hardware knowledge
 - tvspelsfreak for SR conversion to normal map

Testing:
 - Esppiral
 - Alexvgz
 - PkR
 - Derek (ateam) 
 - dakrk
 - neSneSgB
 - woofmute
 - TVi
 - Sappharad
'''

class Pypvr:
    px_modes = {
        0: '1555',  # ARGB1555
        1: '565',  # RGB565
        2: '4444',  # ARGB4444
        3: 'yuv422',  # YUV422
        4: 'bump',  # SR Height map
        5: '555',  # RGB555 - PCX only
        6: 'yuv420',  # YUV420 - same as .SAN format
        7: '8888',  # RGBA8888
        8: 'p4bpp',  # Placeholder 4bpp, pixel format in .PVP file
        9: 'p8bpp',  # Placeholder 8bpp, pixel format in .PVP file
    }

    tex_modes = {
        1: 'tw',  # Twiddled
        2: 'tw mm',  # Twiddled Mips
        3: 'vq',  # VQ
        4: 'vq mm',  # VQ Mips
        5: 'pal4',  # Palette4
        6: 'pal4 mm',  # Palette4 Mips
        7: 'pal8',  # Palette8
        8: 'pal8 mm',  # Palette8 Mips
        9: 're',  # Rectangle
        10: 're mm',  # Reserved - Rectangle can't be mipmapped
        11: 'st',  # Stride
        12: 'st mm',  # Reserved - Stride can't be mipmapped, using "re mm"
        13: 'twre',  # Twiddled Rectangle
        14: 'bmp',  # Bitmap
        15: 'bmp mm',  # Bitmap Mips
        16: 'svq',  # SmallVQ
        17: 'svq mm',  # SmallVQ Mips
        18: 'twal mm'  # Twiddled Alias Mips
    }

    # common twiddle table, there might be better methods but it's fast.
    def twiddle(self, w, h):
        # initialize variables
        index = 0
        arr, h_arr = [], []
        h_inc = Pypvr().init_table()

        # rectangle (horizontal)
        if w > h:
            ratio = int(w / h)

            # print(f'width is {ratio} times height!')

            if w % 32 == 0 and w & (w - 1) != 0 or h & (h - 1) != 0:
                # print('h and w not power of 2. Using Stride format')
                n = h * w
                for i in range(n):
                    arr.append(i)
            else:
                # single block h_inc length
                cur_h_inc = {w: h_inc[0:h - 1] + [2]}  # use height size to define repeating block h_inc

                # define the first horizontal row of image pixel array:
                for j in range(ratio):
                    if w in cur_h_inc:
                        for i in cur_h_inc[w]:
                            h_arr.append(index)
                            index += i
                    index = (len(h_arr) * h)

                # define the vertical row of image pixel array of repeating block:
                v_arr = [int(x / 2) for x in h_arr]
                v_arr = v_arr[0:h]

                for val in v_arr:
                    arr.extend([x + val for x in h_arr])

        # rectangle (vertical)
        elif h > w:
            ratio = int(h / w)
            # print(f'height is {ratio} times width!')

            # set the size of pixel increase array
            cur_h_inc = {w: h_inc[0:w - 1] + [2]}

            # define the first horizontal row of image pixel array:
            if w in cur_h_inc:
                for i in cur_h_inc[w]:
                    h_arr.append(index)
                    index += i

            # define the vertical row of image pixel array:
            v_arr = [int(x / 2) for x in h_arr]

            # repeat vertical array block from the last value of array * h/w ratio
            for i in range(ratio):
                if i == 0:
                    last_val = 0
                else:
                    last_val = arr[-1] + 1

                for val in v_arr:
                    arr.extend([last_val + x + val for x in h_arr])

        elif w == h:  # square
            cur_h_inc = {w: h_inc[0:w - 1] + [2]}
            # define the first horizontal row of image pixel array:
            if w in cur_h_inc:
                for i in cur_h_inc[w]:
                    h_arr.append(index)
                    index += i

            # define the vertical row of image pixel array:
            v_arr = [int(x / 2) for x in h_arr]

            for val in v_arr:
                arr.extend([x + val for x in h_arr])

        return arr

    def init_table(self):
        pat2, h_inc = [], []

        # build Twiddle index table
        seq = np.array([2, 6, 2, 22, 2, 6, 2])
        pat = np.concatenate([seq, [86], seq, [342], seq, [86], seq])

        for i in range(4):
            pat2.extend([1366, 5462, 1366, 21846])
            pat2.extend([1366, 5462, 1366, 87382] if i % 2 == 0 else [1366, 5462, 1366, 349526])

        pat2 = np.array(pat2)

        for i in range(len(pat2)):
            h_inc.extend(np.concatenate([pat, [pat2[i]]]))

        return h_inc

    class Decode:
        def __init__(self, args_str=None, buff_pvr=None, buff_pvp=None):
            self.files_lst = []
            self.out_dir = None
            self.fmt = "png"
            self.flip = ""
            self.log = True
            self.silent = False
            self.debug = False
            self.crc_value = None
            self.log_content = ''
            self.buffer_mode = False
            self.nopvp = False
            self.usepal = None
            self.act_export = False

            self.buffer_pvr = buff_pvr
            self.buffer_pvp = buff_pvp
            self.image_buffer = None

            if args_str:
                # first, check for usepal before processing any other files
                usepal_pattern = r'-usepal\s+"?([^\s"]+\.pvp)"?|"?([^\s"]+\.pvp)"?'
                usepal_match = re.search(usepal_pattern, args_str)
                if usepal_match:
                    self.usepal = usepal_match.group(1) or usepal_match.group(2)
                    # remove the -usepal argument and its value from args_str
                    args_str = re.sub(r'-usepal\s+"?[^\s"]+\.pvp"?\s*', '', args_str)

                # all other patterns
                file_pattern = r'"([^"]+\.(?:pvr|pvp|dat|bin|pvm|tex|mun))"|([^\s]+\.(?:pvr|pvp|dat|bin|pvm|tex|mun))'
                fmt_pattern = r'-fmt\s+(\w+)'
                out_dir_pattern = r'-o\s+"?([^"\s]+(?:\s+[^"\s]+)*)"?'
                flip_pattern = r'-flip'
                silent_flag_pattern = r'-silent'
                nolog_flag_pattern = r'-nolog'
                dbg_flag_pattern = r'-dbg'
                act_flag_pattern = r'-act'
                buffer_pattern = r'-buffer'
                nopvp_pattern = r'-nopvp'

                # extract filenames (PVR or PVP files)
                matches = re.findall(file_pattern, args_str, re.IGNORECASE)
                # non-empty match groups, excluding the usepal file
                self.files_lst = [m[0] if m[0] else m[1] for m in matches if
                                  (m[0] if m[0] else m[1]) != self.usepal]

                fmt_match = re.search(fmt_pattern, args_str)
                if fmt_match:
                    self.fmt = fmt_match.group(1)

                out_dir_match = re.search(out_dir_pattern, args_str)
                if out_dir_match:
                    self.out_dir = out_dir_match.group(1).strip()
                    if not os.path.isabs(self.out_dir):
                        self.out_dir = os.path.abspath(self.out_dir)

                if re.search(flip_pattern, args_str):
                    self.flip = True

                if re.search(silent_flag_pattern, args_str):
                    self.silent = True

                if re.search(nolog_flag_pattern, args_str):
                    self.log = False

                if re.search(dbg_flag_pattern, args_str):
                    self.debug = True

                if re.search(buffer_pattern, args_str):
                    self.buffer_mode = True

                if re.search(nopvp_pattern, args_str):
                    self.nopvp = True

                if re.search(act_flag_pattern, args_str):
                    self.act_export = True

            # if no output directory is specified, default to the directory of the first file
            if not self.out_dir and self.files_lst:
                self.out_dir = os.path.abspath(os.path.dirname(self.files_lst[0]))

            # ensure the output directory exists
            if self.out_dir and not self.buffer_mode:
                os.makedirs(self.out_dir, exist_ok=True)

            # debug info
            if self.debug:
                print(f"Files: {self.files_lst}")
                print(f"Output Directory: {self.out_dir}")
                print(f"Format: {self.fmt}")
                print(f"Flip: {self.flip}")
                print(f"Log: {self.log}")
                print(f"Silent: {self.silent}")
                print(f"Debug: {self.debug}")
                print(f"Buffer: {self.buffer_mode}")
                print(f"USE PVP: {self.usepal}")
                print(f"NO PVP: {self.nopvp}")
                print(f"ACT Export: {self.act_export}")


            if self.buffer_mode and self.buffer_pvr:

                if self.buffer_pvp:
                    act_buffer = self.load_pvp(None, bytearray(), None,self.buffer_pvp)
                else:
                    act_buffer = bytearray()

                self.load_pvr(None, True if self.buffer_pvp else False, act_buffer,None, self.buffer_pvr)

            else:
                for cur_file in self.files_lst:
                    if not cur_file.lower().endswith(('pvp', 'pvr')):

                        print(f"Scanning {cur_file}")
                        try:
                            with open(cur_file, "rb") as f:
                                self.log = True
                                buffer = f.read()

                                # find PVRT and PVPL offsets
                                pvrt_matches = [match.start() for match in re.finditer(b"PVRT", buffer)]
                                pvpl_matches = [match.start() for match in re.finditer(b"PVPL", buffer)]

                                # lists to store offsets and sizes
                                pvrt_offsets_sizes = []
                                pvpl_offsets_sizes = []

                                pvri = 0
                                pvpi = 0
                                apply_palette = False
                                act_buffer = bytearray()

                                # process PVRT matches
                                for offset in pvrt_matches:
                                    if self.debug: print(f"PVRT found at offset: {hex(offset)}")

                                    if offset + 4 < len(buffer):
                                        filesize = int.from_bytes(buffer[offset + 4:offset + 8], byteorder='little') + 8
                                        remaining_bytes = len(buffer) - offset

                                        if filesize > remaining_bytes or filesize < 0x10:
                                            continue
                                    else:
                                        continue

                                    if offset + 11 < len(buffer):

                                        byte_a = buffer[offset + 0xA]
                                        byte_b = buffer[offset + 0xB]
                                        if byte_a != 0x00 or byte_b != 0x00:
                                            continue
                                    else:
                                        continue

                                    pvrt_offsets_sizes.append((offset, filesize))
                                    unpack_dir = os.path.join(self.out_dir, os.path.basename(cur_file) + '_EXT', 'PVR')
                                    os.makedirs(unpack_dir, exist_ok=True)

                                    full_pvr_path = os.path.join(unpack_dir, f"{str(pvri).zfill(3)}.pvr")

                                    # extract file
                                    with open(full_pvr_path, 'wb') as p:
                                        p.write(buffer[offset:offset + filesize])
                                        pvri += 1

                                    self.log_content += (
                                        f"PVR FILE   : {os.path.normpath(full_pvr_path)}\n"
                                        f"CONTAINER  : {os.path.normpath(cur_file)}\n"
                                        f"DATA OFFST : {offset}\n"
                                        f"DATA FSIZE : {filesize}\n"
                                    )

                                    self.load_pvr(full_pvr_path, apply_palette, act_buffer,
                                                  os.path.join(os.path.basename(cur_file) + '_EXT',
                                                               f"{str(pvri - 1).zfill(3)}.pvr"))


                                # process PVPL matches
                                for offset in pvpl_matches:
                                    self.debug: print(f"PVPL found at offset: {hex(offset)}")

                                    if offset + 0xE + 2 <= len(buffer):
                                        value = int.from_bytes(buffer[offset + 0xE:offset + 0xE + 2],
                                                               byteorder='little')
                                        filesize = int.from_bytes(buffer[offset + 4:offset + 8], byteorder='little') + 8

                                        if value not in {0x10, 0x100}:
                                            continue
                                    else:
                                        continue

                                    pvpl_offsets_sizes.append((offset, filesize))
                                    unpack_dir = os.path.join(self.out_dir, os.path.basename(cur_file) + '_EXT', 'PVP')
                                    os.makedirs(unpack_dir, exist_ok=True)

                                    full_pvp_path = os.path.join(unpack_dir, f"{str(pvpi).zfill(3)}.pvp")

                                    # extract file
                                    with open(os.path.join(unpack_dir, f"{str(pvpi).zfill(3)}.pvp"), 'wb') as p:
                                        p.write(buffer[offset:offset + filesize])
                                        pvpi += 1


                                    act_buffer = bytearray()

                                    self.log_content += (
                                        f"PVP FILE   : {os.path.normpath(full_pvp_path)}\n"
                                        f"CONTAINER  : {os.path.normpath(cur_file)}\n"
                                        f"DATA OFFST : {offset}\n"
                                        f"DATA FSIZE : {filesize}\n"
                                    )
                                    self.load_pvp(full_pvp_path, act_buffer, full_pvp_path)

                                print(f"Finished extracting {cur_file}")


                        except FileNotFoundError:
                            print(f"File not found: {cur_file}")
                        except Exception as e:
                            print(f"Error scanning file {cur_file}: {e}")

                    else:
                        full_pvr_path = os.path.abspath(cur_file[:-4] + '.pvr')

                        if self.usepal:

                            full_pvp_path = os.path.abspath(self.usepal)
                        else:
                            full_pvp_path = os.path.abspath(cur_file[:-4] + '.pvp')

                        # print the paths being checked
                        # if not self.silent: print(f"Processing file: {cur_file}")
                        if self.debug: print(f"Checking PVR file: {full_pvr_path}, PVP file: {full_pvp_path}")

                        # check if PVP or PVR file exists
                        pvp_exists = os.path.exists(full_pvp_path)
                        pvr_exists = os.path.exists(full_pvr_path)

                        # debug statements for file existence
                        if self.debug: print(f"PVP exists: {pvp_exists}, PVR exists: {pvr_exists}")

                        apply_palette = True if (cur_file.lower().endswith(".pvp") and pvr_exists) or (
                                cur_file.lower().endswith(".pvr") and pvp_exists) else False

                        act_buffer = bytearray()

                        if pvp_exists:
                            self.load_pvp(full_pvp_path, act_buffer, full_pvp_path)

                        if pvr_exists:
                            self.load_pvr(full_pvr_path, apply_palette, act_buffer, os.path.basename(cur_file))

                if self.log and self.log_content != '':
                    with open(os.path.join(self.out_dir, 'pvr_log.txt'), 'w') as l:
                        l.write(self.log_content)

        def get_image_buffer(self):
            return self.image_buffer


        def read_col(self, px_format, color):

            if px_format == 0:  # ARGB1555
                a = ((color >> 15) & 0x1) * 0xff
                r = int(((color >> 10) & 0x1f) * 0xff / 0x1f)
                g = int(((color >> 5) & 0x1f) * 0xff / 0x1f)
                b = int((color & 0x1f) * 0xff / 0x1f)
                return (r, g, b, a)

            elif px_format == 1:  # RGB565
                a = 0xff
                r = int(((color >> 11) & 0x1f) * 0xff / 0x1f)
                g = int(((color >> 5) & 0x3f) * 0xff / 0x3f)
                b = int((color & 0x1f) * 0xff / 0x1f)
                return (r, g, b, a)

            elif px_format == 2:  # ARGB4444
                a = ((color >> 12) & 0xf) * 0x11
                r = ((color >> 8) & 0xf) * 0x11
                g = ((color >> 4) & 0xf) * 0x11
                b = (color & 0xf) * 0x11
                return (r, g, b, a)

            elif px_format == 5:  # RGB555
                a = 0xFF
                r = int(((color >> 10) & 0x1f) * 0xff / 0x1f)
                g = int(((color >> 5) & 0x1f) * 0xff / 0x1f)
                b = int((color & 0x1f) * 0xff / 0x1f)
                return (r, g, b, a)

            elif px_format in [7]:  # ARGB8888
                a = (color >> 24) & 0xFF
                r = (color >> 16) & 0xFF
                g = (color >> 8) & 0xFF
                b = color & 0xFF
                return (r, g, b, a)

            elif px_format in [14]:  # RGBA8888
                r = (color >> 24) & 0xFF
                g = (color >> 16) & 0xFF
                b = (color >> 8) & 0xFF
                a = color & 0xFF
                return (r, g, b, a)

            elif px_format == 3:

                # YUV422
                yuv0, yuv1 = color

                y0 = (yuv0 >> 8) & 0xFF
                u = yuv0 & 0xFF
                y1 = (yuv1 >> 8) & 0xFF
                v = yuv1 & 0xFF

                # YUV to RGB conversion
                c0 = y0 - 16
                c1 = y1 - 16
                d = u - 128
                e = v - 128

                r0 = max(0, min(255, int((298 * c0 + 409 * e + 128) >> 8)))
                g0 = max(0, min(255, int((298 * c0 - 100 * d - 208 * e + 128) >> 8)))
                b0 = max(0, min(255, int((298 * c0 + 516 * d + 128) >> 8)))

                r1 = max(0, min(255, int((298 * c1 + 409 * e + 128) >> 8)))
                g1 = max(0, min(255, int((298 * c1 - 100 * d - 208 * e + 128) >> 8)))
                b1 = max(0, min(255, int((298 * c1 + 516 * d + 128) >> 8)))

                return r0, g0, b0, r1, g1, b1

        def read_pal(self, mode, color, act_buffer):

            if mode == 4444:
                red = ((color >> 8) & 0xf) << 4
                green = ((color >> 4) & 0xf) << 4
                blue = (color & 0xf) << 4
                alpha = '-'

            if mode == 555:
                red = ((color >> 10) & 0x1f) << 3
                green = ((color >> 5) & 0x1f) << 3
                blue = (color & 0x1f) << 3
                alpha = '-'

            elif mode == 565:
                red = ((color >> 11) & 0x1f) << 3
                green = ((color >> 5) & 0x3f) << 2
                blue = (color & 0x1f) << 3
                alpha = '-'

            elif mode == 8888:
                blue = (color >> 0) & 0xFF
                green = (color >> 8) & 0xFF
                red = (color >> 16) & 0xFF
                alpha = (color >> 24) & 0xFF

            act_buffer += bytes([red, green, blue])
            return act_buffer

        def read_pvp(self, f, act_buffer):

            f.seek(0x08)
            pixel_type = int.from_bytes(f.read(1), 'little')
            if pixel_type == 1:
                mode = 565
            elif pixel_type == 2:
                mode = 4444
            elif pixel_type == 6:
                mode = 8888
            else:
                mode = 555

            f.seek(0x0e)
            ttl_entries = int.from_bytes(f.read(2), 'little')

            f.seek(0x10)  # start palette data
            current_offset = 0x10

            for counter in range(0, ttl_entries):
                if mode != 8888:
                    color = int.from_bytes(f.read(2), 'little')
                    act_buffer = self.read_pal(mode, color, act_buffer)
                    current_offset += 0x2
                else:
                    color = int.from_bytes(f.read(4), 'little')
                    act_buffer = self.read_pal(mode, color, act_buffer)
                    current_offset += 0x4

            return act_buffer, mode, ttl_entries

        def image_flip(self, data, w, h, cmode):

            if cmode == 'RGB':
                pixels_len = 3
            elif cmode == 'RGBA':
                pixels_len = 4
            else:
                pixels_len = 1

            if self.flip:
                data = (np.flipud((np.array(data)).reshape(h, w, -1)).flatten()).reshape(-1, pixels_len).tolist()

            return data


        def save_image(self, file_name, data, bits, w, h, cmode, palette):
            # buffer-mode only: pvr_loader always wraps the decoded image into
            # a PIL Image via PIL_buffer(). File-writing (png/bmp/tga) was
            # removed along with save_png / save_bmp / save_tga.
            self.image_buffer = self.PIL_buffer(file_name, data, bits, w, h, cmode, palette)
            return self.image_buffer

        def PIL_buffer(self, file_name, data, bits, w, h, cmode, palette=None):

            # convert data to PIL
            if 'PAL' in cmode:
                # palette-based images
                if cmode == 'RGB-PAL16':
                    # 4-bit palette (16 colors)
                    data = [item for sublist in data for item in sublist]
                    packed_data = bytearray()
                    for i in range(0, len(data), 2):
                        if i + 1 < len(data):
                            packed_data.append((data[i] << 4) | data[i + 1])
                        else:
                            packed_data.append(data[i] << 4)
                    data = packed_data
                else:
                    # 8-bit palette (256 colors)
                    data = bytes([item for sublist in data for item in sublist])

                # PIL image in 'P' mode
                img = Image.frombytes('P', (w, h), data)

                # palette to PIL format (list of RGB values)
                pil_palette = []
                for color in palette:
                    pil_palette.extend(color[:3])  # Take only RGB components

                img.putpalette(pil_palette)

            else:
                # direct color modes
                if cmode == 'RGB':
                    mode = 'RGB'
                    # convert to RGB bytes
                    pixel_data = bytearray()
                    for pixel in data:
                        pixel_data.extend([pixel[0], pixel[1], pixel[2]])
                    data = bytes(pixel_data)
                elif cmode == 'RGBA':
                    mode = 'RGBA'
                    # convert to RGBA bytes
                    pixel_data = bytearray()
                    for pixel in data:
                        pixel_data.extend([pixel[0], pixel[1], pixel[2], pixel[3]])
                    data = bytes(pixel_data)

                img = Image.frombytes(mode, (w, h), data)

            # CRC if logging is enabled
            if self.log:
                # image to bytes for CRC calculation
                if img.mode == 'P':
                    # use the raw data + palette
                    crc_data = img.tobytes() + bytes(img.palette.getdata()[1])
                else:
                    crc_data = img.tobytes()
                self.crc_value = hex(zlib.crc32(crc_data)).upper()[2:]

            return img

        # TGA does NOT support palettized images!
        def decode_pvr(self, f, file_name, w, h, offset=None, px_format=None, tex_format=None, apply_palette=None,
                       act_buffer=None):
            f.seek(offset)
            data = bytearray()

            if tex_format not in [9, 10, 11, 12, 14, 15]:
                arr = Pypvr().twiddle(w, h)

            if tex_format in [5, 6, 7, 8]:

                cmode = None
                if tex_format in [7, 8]:  # 8bpp
                    palette_entries = 256
                    bits = 8
                    pixels = list(f.read(w * h))
                    data = [pixels[i] for i in arr]

                    if self.flip != '':
                        data = self.image_flip(data, w, h, cmode)
                        # flatten the nested list and convert each value to an integer
                        data = [int(value) for sublist in data for value in sublist]

                    # 4bpp, convert to 8bpp
                else:
                    palette_entries = 16
                    bits = 4
                    pixels = bytearray(f.read(w * h // 2))  # read only required amount of bytes

                    # read 4bpp to 8bpp indexes
                    data = []
                    for i in range(len(pixels)):
                        data.append(((pixels[i]) & 0x0f) * 0x11)  # last 4 bits
                        data.append((((pixels[i]) & 0xf0) >> 4) * 0x11)  # first 4 bits

                    # assuming 'data' contains the 8bpp indexes
                    new_pixels = bytearray(data)

                    # detwiddle 8bpp indexes
                    data = []
                    for num in arr:
                        data.append(new_pixels[num])

                    if self.flip != '':
                        data = self.image_flip(data, w, h, cmode)

                        # flatten the nested list and convert each value to an integer
                        data = [int(value) for sublist in data for value in sublist]

                    data = bytearray(data)  # 8bpp "twiddled data" back into "pixels" variable
                    # convert back to 4bpp indexes with swapped upper and lower bits

                    converted_data = bytearray()
                    for i in range(0, len(data), 2):
                        # swap the position of upper and lower bits
                        index1 = (data[i] // 0x11) << 4 | (data[i + 1] // 0x11)

                        # append the modified index to the converted data
                        converted_data.append(index1)

                    data = converted_data

                data = [data]

                if palette_entries == 16:

                    if apply_palette:
                        palette = [tuple(act_buffer[i:i + 3]) for i in range(0, len(act_buffer), 3)]

                    else:
                        palette = [(i * 17, i * 17, i * 17) for i in range(16)]
                    cmode = 'RGB-PAL16'

                elif palette_entries == 256:
                    if apply_palette:
                        palette = [tuple(act_buffer[i:i + 3]) for i in range(0, len(act_buffer), 3)]

                    else:
                        palette = [(i, i, i) for i in range(256)]
                    cmode = 'RGB-PAL256'

                if self.buffer_mode and self.buffer_pvr:
                    self.image_buffer = self.save_image(file_name, data, bits, w, h, cmode, palette)

                else:
                    self.save_image(file_name, data, bits, w, h, cmode, palette)

            # VQ
            elif tex_format in [3, 4, 16, 17]:

                codebook_size = 256

                # SmallVQ - Thanks Kion! :)

                if tex_format == 16:
                    if w <= 16:
                        codebook_size = 16
                    elif w == 32:
                        codebook_size = 32
                    elif w == 64:
                        codebook_size = 128
                    else:
                        codebook_size = 256

                # SmallVQ + Mips
                elif tex_format == 17:
                    if w <= 16:
                        codebook_size = 16
                    elif w == 32:
                        codebook_size = 64
                    else:
                        codebook_size = 256

                codebook = []

                # BUMP
                if px_format in [4]:
                    cmode = 'RGB'
                    for l in range(codebook_size):
                        block = []
                        for i in range(4):
                            pixel = (int.from_bytes(f.read(2), 'little'))
                            pix_col = self.bump_to_rgb(pixel)
                            block.append(pix_col)

                        codebook.append(block)

                # YUV422
                elif px_format in [3]:
                    cmode = 'RGB'
                    yuv_codebook = []
                    for l in range(codebook_size):
                        block = []
                        for i in range(4):
                            pixel = (int.from_bytes(f.read(2), 'little'))
                            block.append(pixel)

                        r0, g0, b0, r1, g1, b1 = self.read_col(px_format, (block[0], block[3]))
                        r2, g2, b2, r3, g3, b3 = self.read_col(px_format, (block[1], block[2]))

                        yuv_codebook.append([(r0, g0, b0), (r2, g2, b2), (r3, g3, b3), (r1, g1, b1)])

                    codebook = yuv_codebook

                else:
                    cmode = 'RGBA'
                    for l in range(codebook_size):
                        block = []
                        for i in range(4):
                            pixel = (int.from_bytes(f.read(2), 'little'))
                            pix_col = self.read_col(px_format, pixel)
                            block.append(pix_col)

                        codebook.append(block)

                # VQ Mips!
                if tex_format in [4, 17]:

                    pvr_dim = [4, 8, 16, 32, 64, 128, 256, 512, 1024]
                    mip_size = [0x10, 0x40, 0x100, 0x400, 0x1000, 0x4000, 0x10000, 0x40000]
                    size_adjust = {4: 1, 17: 1}  # 8bpp size is 4bpp *2
                    extra_mip = {4: 0x6, 17: 0x6, }  # smallest mips fixed size

                    for i in range(len(pvr_dim)):
                        if pvr_dim[i] == w:
                            mip_index = i - 1
                            break

                    # skip mips for image data offset
                    mip_sum = (sum(mip_size[:mip_index]) * size_adjust[tex_format]) + (extra_mip[tex_format])
                    f.seek(f.tell() + mip_sum)

                # read pixel_index:
                pixel_list = []
                bytes_to_read = int((w * h) / 4)

                # each index stores 4 pixels
                for i in range(bytes_to_read):
                    pixel_index = (int.from_bytes(f.read(1), 'little'))
                    pixel_list.append(int(pixel_index))

                # detwiddle image data indices, put them into arr list
                arr = Pypvr().twiddle(int(w / 2), int(h / 2))

                # create an empty 2D array to store pixel data
                image_array = [[(0, 0, 0, 0) for _ in range(w)] for _ in range(h)]

                # iterate over the blocks and update the pixel values in the array
                i = 0
                for y in range(h // 2):
                    for x in range(w // 2):
                        image_array[y * 2][x * 2] = codebook[pixel_list[arr[i]]][0]
                        image_array[y * 2 + 1][x * 2] = codebook[pixel_list[arr[i]]][1]
                        image_array[y * 2][x * 2 + 1] = codebook[pixel_list[arr[i]]][2]
                        image_array[y * 2 + 1][x * 2 + 1] = codebook[pixel_list[arr[i]]][3]
                        i += 1

                # flatten the 2D array to a 1D list for putdata
                data = [pixel for row in image_array for pixel in row]
                if self.flip != '':
                    data = self.image_flip(data, w, h, cmode)

                palette = ''
                # save the image
                self.save_image(file_name, data, 8, w, h, cmode, palette)

            # BMP ABGR8888
            elif tex_format in [14, 15]:
                pixels = [int.from_bytes(f.read(4), 'little') for _ in range(w * h)]
                data = [(self.read_col(14, p)) for p in pixels]

                palette = ''
                cmode = 'RGBA'

                if self.flip != '':
                    data = self.image_flip(data, w, h, cmode)

                # save the image
                self.save_image(file_name, data, 8, w, h, cmode, palette)

            # BUMP loop
            elif px_format == 4:
                pixels = [int.from_bytes(f.read(2), 'little') for _ in range(w * h)]
                data = [self.bump_to_rgb(p) for p in (pixels[i] for i in arr)]

                palette = ''
                cmode = 'RGB'

                if self.flip != '':
                    data = self.image_flip(data, w, h, cmode)

                # save the image
                self.save_image(file_name, data, 8, w, h, cmode, palette)

            # ARGB modes
            elif px_format in [0, 1, 2, 5, 7, 18]:

                pixels = [int.from_bytes(f.read(2), 'little') for _ in range(w * h)]

                if tex_format not in [9, 10, 11, 12, 14, 15]:  # If Twiddled
                    data = [(self.read_col(px_format, p)) for p in (pixels[i] for i in arr)]
                else:
                    data = [(self.read_col(px_format, p)) for p in pixels]

                palette = ''
                cmode = 'RGBA'

                if self.flip != '':
                    data = self.image_flip(data, w, h, cmode)

                # save the image
                self.save_image(file_name, data, 8, w, h, cmode, palette)

            # YUV420 modes
            elif px_format in [6]:
                data = []
                self.yuv420_to_rgb(f, w, h, data)

                palette = ''
                cmode = 'RGB'

                if self.flip != '':
                    data = self.image_flip(data, w, h, cmode)

                # save the image
                self.save_image(file_name, data, 8, w, h, cmode, palette)

            # YUV422 modes
            elif px_format in [3]:
                data = []

                # twiddled
                if tex_format not in [9, 10, 11, 12, 14, 15]:
                    i = 0
                    offset = f.tell()

                    for y in range(h):
                        for x in range(0, w, 2):
                            f.seek(offset + (arr[i] * 2))
                            yuv0 = int.from_bytes(f.read(2), 'little')
                            i += 1
                            f.seek(offset + (arr[i] * 2))
                            yuv1 = int.from_bytes(f.read(2), 'little')
                            r0, g0, b0, r1, g1, b1 = self.read_col(px_format, (yuv0, yuv1))
                            data.append((r0, g0, b0))
                            data.append((r1, g1, b1))
                            i += 1

                else:
                    for y in range(h):
                        for x in range(0, w, 2):
                            # read yuv0 and yuv1 separately
                            yuv0 = int.from_bytes(f.read(2), 'little')
                            yuv1 = int.from_bytes(f.read(2), 'little')
                            r0, g0, b0, r1, g1, b1 = self.read_col(px_format, (yuv0, yuv1))
                            data.append((r0, g0, b0))
                            data.append((r1, g1, b1))

                palette = ''
                cmode = 'RGB'

                if self.flip != '':
                    data = self.image_flip(data, w, h, cmode)

                # save the image
                self.save_image(file_name, data, 8, w, h, cmode, palette)


        def load_pvr(self, PVR_file, apply_palette, act_buffer, file_name,buffer_pvr=None):
            px_modes = Pypvr().px_modes
            tex_modes = Pypvr().tex_modes

            try:
                if buffer_pvr:
                    f_buffer = io.BytesIO(buffer_pvr)
                else:
                    with open(PVR_file, 'rb') as f:
                        f_buffer = io.BytesIO(f.read())

                header_data = f_buffer.getvalue()
                gbix_offset = header_data.find(b"GBIX")

                if gbix_offset != -1:
                    f_buffer.seek(gbix_offset + 0x4)
                    gbix_size = int.from_bytes(f_buffer.read(4), byteorder='little')
                    if gbix_size == 0x8:
                        gbix_val1 = int.from_bytes(f_buffer.read(4), byteorder='little')
                        gbix_val2 = int.from_bytes(f_buffer.read(4), byteorder='little')
                        if self.debug:
                            print(hex(gbix_val1), hex(gbix_val2))
                    elif gbix_size == 0x4:
                        gbix_val1 = int.from_bytes(f_buffer.read(4), byteorder='little')
                        gbix_val2 = ''
                    else:
                        print('invalid or unsupported GBIX size:', gbix_size, file_name)
                else:
                    if self.debug:
                        print('GBIX found at:', hex(gbix_offset)) if gbix_offset != -1 else print('GBIX not found')

                    gbix_val1 = ''
                    gbix_val2 = ''


                offset = header_data.find(b"PVRT")
                if offset != -1 or len(header_data) < 0x10:
                    f_buffer.seek(offset + 0x8)

                    # pixel format
                    px_format = int.from_bytes(f_buffer.read(1), byteorder='little')
                    tex_format = int.from_bytes(f_buffer.read(1), byteorder='little')

                    f_buffer.seek(f_buffer.tell() + 2)

                    # image size
                    w = int.from_bytes(f_buffer.read(2), byteorder='little')
                    h = int.from_bytes(f_buffer.read(2), byteorder='little')
                    offset = f_buffer.tell()

                    if self.debug:
                        print(PVR_file.split('/')[-1], 'size:', w, 'x', h, 'format:',
                              f'[{tex_format}] {tex_modes[tex_format]}', f'[{px_format}] {px_modes[px_format]}')

                    if tex_format in [2, 4, 6, 8, 10, 12, 15, 17, 18]:
                        if tex_format in [2, 6, 8, 10, 15, 18]:
                            # Mips skip
                            pvr_dim = [4, 8, 16, 32, 64, 128, 256, 512, 1024]
                            mip_size = [0x20, 0x80, 0x200, 0x800, 0x2000, 0x8000, 0x20000, 0x80000]
                            size_adjust = {2: 4, 6: 1, 8: 2, 10: 4, 15: 8, 18: 4}  # 8bpp size is 4bpp *2
                            extra_mip = {2: 0x2c, 6: 0xc, 8: 0x18, 10: 0x2c, 15: 0x54,
                                         18: 0x30}  # smallest mips fixed size

                            for i in range(len(pvr_dim)):
                                if pvr_dim[i] == w:
                                    mip_index = i - 1
                                    break

                            mip_sum = (sum(mip_size[:mip_index]) * size_adjust[tex_format]) + (
                                extra_mip[tex_format])

                            offset += mip_sum

                    self.decode_pvr(f_buffer, file_name, w, h, offset, px_format, tex_format, apply_palette,
                                    act_buffer)

                    # LOG stuff for later reimport
                    if self.log:
                        self.log_content += (
                            f"IMAGE FILE : {os.path.normpath(os.path.join(self.out_dir, file_name))[:-4]}.{self.fmt}\n"
                            f"TARGET DIR : {os.path.normpath(os.path.dirname(PVR_file))}\n"
                            f"ENC PARAMS : {' '.join(f'-{mode}' for mode in tex_modes[tex_format].split())}"
                            f" -{px_modes[px_format]}"
                            f"{' -flip' if self.flip else ''}"
                            f"{' -nopvp' if self.nopvp else ''}"
                            f"{f' -gi {gbix_val1}' if gbix_val1 else ''}"
                            f"{f' -gitrim' if not gbix_val2 and gbix_val1 else ''}"
                            f" \nIMAGE SIZE : {w}x{h}\nDATA CRC32 : {self.crc_value}\n"
                            f"---------------\n"
                        )

                else:
                    print(f"{self.out_dir}\\{PVR_file} --> ERROR!  PVRT header not found!")

            except Exception:
                if not self.image_buffer: print(f'{self.out_dir}\\{PVR_file} --> ERROR!  ')

        def load_pvp(self, PVP_file, act_buffer, file_name,pvp_buffer = None):

            try:

                if pvp_buffer:
                    f_buffer = io.BytesIO(pvp_buffer)
                else:
                    with open(PVP_file, 'rb') as f:
                        f_buffer = io.BytesIO(f.read())

                file_size = len(f_buffer.read())
                f_buffer.seek(0x0)
                PVP_check = f_buffer.read(4)

                if PVP_check == b'PVPL' and file_size > 0x10:  # PVPL header and size are OK!
                        act_buffer, mode, ttl_entries = self.read_pvp(f_buffer, act_buffer)
                        # write_act removed: pvr_loader runs in buffer mode and never exports ACT palettes.
                else:
                    print('Invalid .PVP file!')  # skip this file

            except:
                print(f'PVP data error! {PVP_file}')
            return act_buffer




        def bump_to_rgb(self, SR_value):
            # process SR value
            S = (1.0 - ((SR_value >> 8) / 255.0)) * math.pi / 2
            R = (SR_value & 0xFF) / 255.0 * 2 * math.pi - 2 * math.pi * (SR_value & 0xFF > math.pi)
            red = (math.sin(S) * math.cos(R) + 1.0) * 0.5
            green = (math.sin(S) * math.sin(R) + 1.0) * 0.5
            blue = (math.cos(S) + 1.0) * 0.5

            # convert to RGB values
            return (
                int(red * 255),
                int(green * 255),
                int(blue * 255)
            )

        def yuv420_to_rgb(self, f, w, h, data):
            # precompute conversion coefficients
            u_offset = -128
            v_offset = -128
            r_factor = 1.402
            g_u_factor = -0.344136
            g_v_factor = -0.714136
            b_factor = 1.772

            # initialize RGB buffer
            rgb_data = np.zeros((h, w, 3), dtype=np.uint8)

            # calculate the number of macroblocks
            mb_width = w // 16
            mb_height = h // 16

            # loop over each macroblock (16x16 pixels)
            for mb_y in range(mb_height):
                for mb_x in range(mb_width):
                    # read U and V data for the 16x16 block (8x8 U and V values)
                    u_block = np.frombuffer(f.read(64), dtype=np.uint8).reshape((8, 8))
                    v_block = np.frombuffer(f.read(64), dtype=np.uint8).reshape((8, 8))

                    # read Y data for the four 8x8 blocks (Y0, Y1, Y2, Y3)
                    y_blocks = [np.frombuffer(f.read(64), dtype=np.uint8).reshape((8, 8)) for _ in range(4)]

                    # upscale U and V to 16x16 to match the 16x16 Y blocks using np.kron (faster than np.repeat)
                    u_block = np.kron(u_block, np.ones((2, 2), dtype=np.uint8))
                    v_block = np.kron(v_block, np.ones((2, 2), dtype=np.uint8))

                    # prepare Y data for the full 16x16 block
                    full_y = np.zeros((16, 16), dtype=np.uint8)
                    full_y[:8, :8] = y_blocks[0]
                    full_y[:8, 8:] = y_blocks[1]
                    full_y[8:, :8] = y_blocks[2]
                    full_y[8:, 8:] = y_blocks[3]

                    # convert U, V, and Y to RGB in a vectorized manner
                    u_block = u_block + u_offset
                    v_block = v_block + v_offset
                    r = np.clip(full_y + r_factor * v_block, 0, 255).astype(np.uint8)
                    g = np.clip(full_y + g_u_factor * u_block + g_v_factor * v_block, 0, 255).astype(np.uint8)
                    b = np.clip(full_y + b_factor * u_block, 0, 255).astype(np.uint8)

                    # assign RGB values to the final RGB buffer
                    rgb_data[mb_y * 16:(mb_y + 1) * 16, mb_x * 16:(mb_x + 1) * 16, 0] = r
                    rgb_data[mb_y * 16:(mb_y + 1) * 16, mb_x * 16:(mb_x + 1) * 16, 1] = g
                    rgb_data[mb_y * 16:(mb_y + 1) * 16, mb_x * 16:(mb_x + 1) * 16, 2] = b

            # convert rgb_data to a list of RGB tuples
            data.extend(tuple(rgb_data[y, x]) for y in range(h) for x in range(w))

            return data
