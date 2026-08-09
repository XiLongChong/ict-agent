import { createApp } from "vue";
import { createVuetify } from "vuetify";
import "vuetify/styles";
import "@mdi/font/css/materialdesignicons.css";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/dm-sans/700.css";
import "@fontsource/jetbrains-mono/500.css";
import App from "./App.vue";
import "./styles.css";

const vuetify = createVuetify({
  defaults: {
    VBtn: { rounded: "lg", elevation: 0 },
    VCard: { rounded: "lg", elevation: 0 },
    VChip: { rounded: "lg" },
    VTextField: { density: "compact", variant: "outlined", hideDetails: "auto" },
    VSelect: { density: "compact", variant: "outlined", hideDetails: "auto" },
    VTextarea: { density: "compact", variant: "outlined", hideDetails: "auto" },
  },
  theme: {
    defaultTheme: "googleLight",
    themes: {
      googleLight: {
        dark: false,
        colors: {
          background: "#ffffff",
          surface: "#ffffff",
          primary: "#4285f4",
          secondary: "#dbeafe",
          error: "#ea4335",
          warning: "#fbbc05",
          success: "#34a853",
          info: "#0043ad",
        },
      },
    },
  },
});

createApp(App).use(vuetify).mount("#app");
