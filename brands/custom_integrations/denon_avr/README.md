# Brand assets for the home-assistant/brands PR

Home Assistant serves the icon/logo shown in the "add integration" dialog and on
the integration/device pages centrally from https://github.com/home-assistant/brands,
keyed by the manifest `domain` (`denon_avr`). It cannot be shipped inside this
integration. To make the icon match the official Denon integration, submit these
files to the brands repository.

## Files to add (place the PNGs in this folder, then open the PR)

```
custom_integrations/denon_avr/icon.png       256 x 256 px
custom_integrations/denon_avr/icon@2x.png    512 x 512 px
custom_integrations/denon_avr/logo.png       (optional) max 240 px height
custom_integrations/denon_avr/logo@2x.png    (optional) 2x of logo.png
```

Requirements (per the brands repository):
- PNG, transparent background, trimmed of surrounding whitespace.
- `icon` must be square; `logo` keeps its aspect ratio (wordmark).
- `@2x` variants are exactly double the base resolution.

Use the Denon brand artwork so it matches the official `denonavr` integration
(whose brand lives under `denonavr/` in the brands repo). Note that the Denon
logo is a trademark of its owner; only submit artwork you are permitted to use.

## PR steps

1. Fork https://github.com/home-assistant/brands and clone your fork.
2. Copy this `custom_integrations/denon_avr/` folder (with the PNGs added) into
   the fork at the same path.
3. Run the repo's image checks if available, commit, push, and open a pull
   request to home-assistant/brands.

Until the PR is merged, Home Assistant shows the generic integration icon.
