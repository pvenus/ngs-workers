import unittest

from PIL import Image, ImageDraw

from image_ops import (
    crop_component,
    find_components,
    remove_background,
    remove_background_from_corners,
    select_connected_layer,
)


class ImageOpsTests(unittest.TestCase):
    def test_exact_color_contiguous_removal(self):
        image = Image.new("RGBA", (5, 5), "white")
        ImageDraw.Draw(image).rectangle((1, 1, 3, 3), fill="red")
        result = remove_background(image, (0, 0), tolerance=0, contiguous=True)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((2, 2)), (255, 0, 0, 255))

    def test_global_removal_removes_disconnected_matching_color(self):
        image = Image.new("RGBA", (3, 1), "white")
        image.putpixel((1, 0), (0, 0, 0, 255))
        result = remove_background(image, (0, 0), tolerance=0, contiguous=False)
        self.assertEqual([result.getpixel((x, 0))[3] for x in range(3)], [0, 255, 0])

    def test_components_and_crop(self):
        image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((1, 1, 2, 2), fill="red")
        draw.rectangle((6, 5, 8, 7), fill="blue")
        components = find_components(image, min_pixels=1)
        self.assertEqual(len(components), 2)
        self.assertEqual(components[0].box, (6, 5, 9, 8))
        self.assertEqual((components[0].seed_x, components[0].seed_y), (6, 5))
        self.assertEqual(crop_component(image, components[0]).size, (3, 3))

    def test_connected_selection_copies_only_selected_pixels(self):
        image = Image.new("RGBA", (8, 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 2, 2), fill="red")
        draw.rectangle((6, 1, 7, 3), fill="blue")
        layer, component = select_connected_layer(image, (1, 1))
        self.assertEqual(component.box, (0, 0, 3, 3))
        self.assertEqual(layer.size, (3, 3))
        self.assertEqual(layer.getpixel((1, 1)), (255, 0, 0, 255))

    def test_four_corner_background_removal(self):
        image = Image.new("RGBA", (9, 9), (250, 249, 247, 255))
        ImageDraw.Draw(image).rectangle((3, 3, 5, 5), fill=(30, 30, 30, 255))
        result = remove_background_from_corners(image, tolerance=4, feather=0)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((8, 8))[3], 0)
        self.assertEqual(result.getpixel((4, 4))[3], 255)

    def test_alpha_threshold_separates_faint_bridge(self):
        image = Image.new("RGBA", (7, 3), (0, 0, 0, 0))
        image.putpixel((1, 1), (255, 0, 0, 255))
        image.putpixel((2, 1), (255, 0, 0, 255))
        image.putpixel((3, 1), (255, 0, 0, 40))
        image.putpixel((4, 1), (0, 0, 255, 255))
        image.putpixel((5, 1), (0, 0, 255, 255))
        low = find_components(image, alpha_threshold=1, min_pixels=1)
        high = find_components(image, alpha_threshold=80, min_pixels=1)
        self.assertEqual(len(low), 1)
        self.assertEqual(len(high), 2)


if __name__ == "__main__":
    unittest.main()
