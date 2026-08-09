const componentBase =
  "https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@3.11.0/dist-cdn/components";

document.documentElement.dataset.components = "loading";

try {
  await Promise.all([
    import(`${componentBase}/badge/badge.js`),
    import(`${componentBase}/callout/callout.js`),
    import(`${componentBase}/card/card.js`),
    import(`${componentBase}/details/details.js`),
    import(`${componentBase}/icon/icon.js`),
    import(`${componentBase}/progress-bar/progress-bar.js`),
    import(`${componentBase}/spinner/spinner.js`),
    import(`${componentBase}/tag/tag.js`),
  ]);
  document.documentElement.dataset.components = "ready";
} catch (error) {
  document.documentElement.dataset.components = "error";
  document.documentElement.dataset.componentError = String(error);
  throw error;
}
