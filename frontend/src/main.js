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
    defaultTheme: "tailAdminLight",
    themes: {
      tailAdminLight: {
        dark: false,
        colors: {
          background: "#f9fafb",
          surface: "#ffffff",
          primary: "#465fff",
          secondary: "#dde9ff",
          error: "#d92d20",
          warning: "#f79009",
          success: "#039855",
          info: "#475467",
        },
      },
    },
  },
});

createApp(App).use(vuetify).mount("#app");
