# Brand icon / logo (for publishing)

The icon shown in the "add integration" dialog and on the integration/device
pages is served centrally from the home-assistant/brands repository, keyed by
the manifest `domain` (here: `denon_avr`). It cannot be shipped locally in this
custom_components folder.

To make the icon match the official Denon integration when publishing, submit a
pull request to https://github.com/home-assistant/brands with a custom
integration entry:

```
custom_integrations/denon_avr/icon.png       # 256x256 px, PNG, trimmed, transparent
custom_integrations/denon_avr/icon@2x.png    # 512x512 px
custom_integrations/denon_avr/logo.png       # optional, max height 240 px
custom_integrations/denon_avr/logo@2x.png    # optional
```

Use the Denon brand artwork so it matches the official `denonavr` integration
(whose brand lives at custom/core `denonavr/`). Follow the brands repository
image requirements (square icons, transparent background, exact sizes).

Until this PR is merged, Home Assistant shows the generic integration icon.
