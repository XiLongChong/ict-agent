import { createApp } from "vue";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/500.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/dm-sans/700.css";
import "@fontsource/jetbrains-mono/500.css";
import App from "./App.vue";
import router from "./router";
import "./styles.css";

createApp(App).use(router).mount("#app");
