import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

export function useResponsiveChart() {
  const chartRef = ref(null);
  const chartHostRef = ref(null);
  let observer;
  let animationFrame;
  let lastWidth = 0;
  let pendingWidth = 0;
  let updating = false;

  async function resizeChart(width) {
    const nextWidth = Math.round(width);
    if (!chartRef.value || updating || nextWidth <= 0 || nextWidth === lastWidth) return;
    lastWidth = nextWidth;
    updating = true;
    try {
      await chartRef.value.refresh();
    } finally {
      updating = false;
    }
    if (Math.round(pendingWidth) !== lastWidth) resizeChart(pendingWidth);
  }

  onMounted(async () => {
    await nextTick();
    const hostStyle = getComputedStyle(chartHostRef.value);
    lastWidth = Math.round(
      chartHostRef.value.clientWidth - Number.parseFloat(hostStyle.paddingLeft) - Number.parseFloat(hostStyle.paddingRight)
    );
    observer = new ResizeObserver(([entry]) => {
      pendingWidth = entry.contentRect.width;
      if (animationFrame) return;
      animationFrame = requestAnimationFrame(() => {
        animationFrame = undefined;
        resizeChart(pendingWidth);
      });
    });
    observer.observe(chartHostRef.value);
  });

  onBeforeUnmount(() => {
    observer?.disconnect();
    cancelAnimationFrame(animationFrame);
  });

  return { chartRef, chartHostRef };
}
