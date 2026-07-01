# Brand images

Since Home Assistant 2026.2 (the brands proxy API), a custom integration can
ship its own brand images locally, and they take priority over the central
brands CDN. No pull request to home-assistant/brands is needed.

The images live in `custom_components/denon_avr/brand/`:

```
brand/icon.png            brand/icon@2x.png
brand/logo.png            brand/logo@2x.png
brand/dark_icon.png       brand/dark_icon@2x.png
brand/dark_logo.png       brand/dark_logo@2x.png
```

They were taken from the official Denon (`denonavr`) brand on the Home Assistant
brands CDN (https://brands.home-assistant.io/denonavr/) so the icon matches the
built-in Denon integration.

## Trademark notice

The Denon name and logo are trademarks of their respective owner. These brand
images are included only to identify the compatible hardware, following Home
Assistant's brand-image mechanism. They are NOT covered by this project's MIT
license and remain the property of the trademark owner.
