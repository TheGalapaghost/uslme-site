import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';
import mdx from '@astrojs/mdx';

import cloudflare from "@astrojs/cloudflare";

export default defineConfig({
  // ← update to your actual domain
  site: 'https://usmleprep.guide',

  integrations: [sitemap(), mdx()],

  markdown: {
    shikiConfig: {
      theme: 'github-light',
    },
  },

  output: "hybrid",
  adapter: cloudflare()
});