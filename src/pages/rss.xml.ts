import rss from '@astrojs/rss';
import type { APIContext } from 'astro';

export async function GET(context: APIContext) {
  const postFiles = import.meta.glob('../../posts/*.md', { eager: true }) as Record<string, any>;

  const posts = Object.values(postFiles)
    .map((post) => ({
      title: post.frontmatter.title,
      description: post.frontmatter.description,
      pubDate: new Date(post.frontmatter.date),
      link: `/blog/${post.frontmatter.slug || post.file.split('/').pop()?.replace('.md', '')}/`,
    }))
    .sort((a, b) => b.pubDate.getTime() - a.pubDate.getTime());

  return rss({
    title: 'USMLE Prep Guide',
    description:
      'Expert USMLE exam prep guides for Step 1, Step 2, and Step 3. Evidence-based strategies, mnemonics, and resource reviews.',
    site: context.site!.toString(),
    items: posts,
  });
}
