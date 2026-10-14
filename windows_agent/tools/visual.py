"""Local screenshot perception, without cloud providers or action executors."""


def inspect_screen(goal='', check=lambda: None):
    from screen_use.perception import screen, som, uia_tree

    check()
    elements = uia_tree.list_ui_elements()
    check()
    image, scale = screen.downscale(screen.screenshot(), 896)
    image = som.annotate(image, elements, scale)
    encoded, quality = screen.to_jpeg_base64_capped(image, 75, 90 * 1024)
    check()
    return {'image_base64': encoded, 'width': image.width, 'height': image.height,
            'scale': scale, 'quality': quality,
            'foreground_title': getattr(elements, 'foreground_title', ''),
            'elements': [element.to_dict() for element in elements[:60]],
            'goal': goal[:500],
            'notice': 'Annotated screenshot for the local model. These marks are not desktop_agent element IDs. No cloud model was invoked.'}
