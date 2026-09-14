#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Preview optional notification clips and a pickup-gated keymap without HID.

Requires Pillow and abralia-desktop. Uses the production broker and renderer.
"""

import argparse
from pathlib import Path

from PIL import Image

from abralia.backend.core import Broker, Caller
from abralia.backend.device import DeviceDriver
from notification_fog_preview import keyboard_image


def scene():
    now = [0.]
    broker = Broker(clock=lambda: now[0])
    driver = DeviceDriver(broker, 'builtin:keychron-v3-8k-ansi-encoder-effect25', 'simulated')
    owner = Caller('preview')
    token = broker.call(owner, 'acquire_slot', {'label':'Preview','idempotency_key':'a'})['allocation']['slot_token']
    broker.set_active(True)
    return now, broker, driver, owner, token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    stills = []
    for shape in ('ring','spot','sweep','pulse'):
        now, broker, driver, owner, token = scene()
        animation = {'duration_ms':3000,'layers':[{'shape':shape,'color':'positive','opacity':.65}]}
        broker.call(owner,'set_notification',{'slot_token':token,'enabled':True,'animation':animation,'idempotency_key':'n'})
        frames = []
        for tick in range(151):
            now[0] = .58 + tick / 15
            broker.step()
            frame = driver.renderer.frame(broker).payload
            image = keyboard_image(driver.profile,frame,f'{shape} | {now[0]:.2f}s after call | algorithm preview')
            frames.append(image)
            if tick in (70,90,110):
                stills.append(image)
        frames[0].save(args.output/f'{shape}.gif',save_all=True,append_images=frames[1:],duration=67,loop=0)
    now, broker, driver, owner, token = scene()
    result = broker.call(owner,'show_keyboard_frame',{'slot_token':token,'colors':{
        'W':'20A0FF','A':'20A0FF','S':'20A0FF','D':'20A0FF','SPACE':'positive'},'idempotency_key':'guide'})
    stills.append(keyboard_image(driver.profile,driver.renderer.frame(broker).payload,'Keymap requested: awaiting pickup'))
    broker.select_slot(token)
    stills.append(keyboard_image(driver.profile,driver.renderer.frame(broker).payload,'After pickup: WASD + Space guide; Esc closes'))
    broker.dismiss_keyboard_frame(token,result['allocation']['keyboard_frame']['id'])
    stills.append(keyboard_image(driver.profile,driver.renderer.frame(broker).payload,'After Esc: ordinary Abralia lighting restored'))
    sheet = Image.new('RGB',(stills[0].width*3,stills[0].height*((len(stills)+2)//3)),'#172031')
    for i, image in enumerate(stills):
        sheet.paste(image,((i%3)*image.width,(i//3)*image.height))
    sheet.save(args.output/'custom-visuals.png')
    print(args.output)


if __name__=='__main__':
    main()
