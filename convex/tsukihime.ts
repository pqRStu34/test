import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

export const getPendingReleases = query({
  args: { limit: v.optional(v.number()) },
  handler: async (ctx, args) => {
    const limit = args.limit ?? 25;
    return await ctx.db
      .query("tsukihime_releases")
      .withIndex("by_status", (q) => q.eq("status", "pending"))
      .take(limit);
  },
});

export const addReleases = mutation({
  args: {
    releases: v.array(
      v.object({
        title: v.string(),
        category: v.string(),
        link: v.string(),
        fileSize: v.optional(v.number()),
        seeders: v.optional(v.number()),
      })
    ),
  },
  handler: async (ctx, args) => {
    let addedCount = 0;
    const now = new Date().toISOString();

    for (const item of args.releases) {
      const existing = await ctx.db
        .query("tsukihime_releases")
        .withIndex("by_link", (q) => q.eq("link", item.link))
        .first();

      if (!existing) {
        await ctx.db.insert("tsukihime_releases", {
          title: item.title,
          category: item.category,
          link: item.link,
          fileSize: item.fileSize,
          seeders: item.seeders,
          status: "pending",
          addedTime: now,
        });
        addedCount++;
      }
    }
    return addedCount;
  },
});

export const updateUploadStatus = mutation({
  args: {
    link: v.string(),
    telegramMessageId: v.optional(v.string()),
    telegramFileUniqueId: v.optional(v.string()),
    status: v.string(),
  },
  handler: async (ctx, args) => {
    const record = await ctx.db
      .query("tsukihime_releases")
      .withIndex("by_link", (q) => q.eq("link", args.link))
      .first();

    if (record) {
      await ctx.db.patch(record._id, {
        telegramMessageId: args.telegramMessageId,
        telegramFileUniqueId: args.telegramFileUniqueId,
        status: args.status,
      });
      return true;
    }
    return false;
  },
});
