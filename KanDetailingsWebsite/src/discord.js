// The Discord bridge.
//
// What it does:
//   * posts a card into #bookings the moment a booking lands
//   * opens a thread per booking; anything staff type in that thread is written
//     back into the customer's chat on the website
//   * anything the customer types on the website is mirrored into the thread
//   * thread commands: !confirm <1-4> | !status <state> | !info | !help
//
// Requires the "Message Content Intent" to be switched on for the bot in the
// Discord developer portal — without it the bot cannot read staff replies.

import {
  ChannelType,
  Client,
  EmbedBuilder,
  Events,
  GatewayIntentBits,
  Partials,
} from 'discord.js';
import { formatSlot, SHOP_TIMEZONE } from './scheduling.js';

const STATUSES = ['pending', 'confirmed', 'in_progress', 'completed', 'cancelled'];

/** A no-op bridge, used when the bot is not configured. */
const DISABLED = {
  enabled: false,
  announceBooking: async () => {},
  relayCustomerMessage: async () => {},
  relayStatusChange: async () => {},
};

export async function startDiscordBridge({ store, onStaffMessage, onCommand }) {
  const token = process.env.DISCORD_TOKEN;
  if (!token) {
    console.log('[discord] DISCORD_TOKEN not set — running without the Discord bridge.');
    return DISABLED;
  }

  const bookingsChannelId = process.env.DISCORD_BOOKINGS_CHANNEL_ID;
  const chatChannelId = process.env.DISCORD_CHAT_CHANNEL_ID || bookingsChannelId;
  if (!bookingsChannelId) {
    console.warn('[discord] DISCORD_BOOKINGS_CHANNEL_ID not set — bridge disabled.');
    return DISABLED;
  }

  const client = new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMessages,
      GatewayIntentBits.MessageContent,
    ],
    partials: [Partials.Channel, Partials.Message],
  });

  const ready = new Promise((resolve) => client.once(Events.ClientReady, resolve));

  client.on(Events.MessageCreate, async (message) => {
    try {
      await handleStaffMessage(message);
    } catch (err) {
      console.error('[discord] failed to handle message:', err);
    }
  });

  client.on(Events.Error, (err) => console.error('[discord] client error:', err));

  await client.login(token);
  await ready;
  console.log(`[discord] logged in as ${client.user.tag}`);

  async function handleStaffMessage(message) {
    if (message.author.bot) return;
    if (!message.channel.isThread()) return;

    const booking = store.getBookingByThread(message.channel.id);
    if (!booking) return;

    const content = (message.content || '').trim();
    if (!content) return;

    if (content.startsWith('!')) {
      const [command, ...rest] = content.slice(1).split(/\s+/);
      const reply = await runCommand(booking, command.toLowerCase(), rest, message);
      if (reply) await message.reply(reply);
      return;
    }

    const staffName = message.member?.displayName || message.author.globalName || message.author.username;
    onStaffMessage({
      bookingId: booking.id,
      authorName: staffName,
      body: content,
      discordMessageId: message.id,
    });
    await message.react('📨').catch(() => {});
  }

  async function runCommand(booking, command, args, message) {
    switch (command) {
      case 'help':
        return [
          '**Thread commands**',
          '`!confirm <1-4>` — lock in that ranked time slot and tell the customer',
          `\`!status <${STATUSES.join('|')}>\` — move the booking along`,
          '`!info` — reprint the booking details',
          'Anything else you type here goes straight to the customer on the website.',
        ].join('\n');

      case 'confirm': {
        const choice = Number(args[0]);
        if (!Number.isInteger(choice) || choice < 1 || choice > booking.slots.length) {
          return `Pick a ranked choice between 1 and ${booking.slots.length} — e.g. \`!confirm 2\`.`;
        }
        const slot = booking.slots.find((s) => s.preference === choice);
        const staffName = message.member?.displayName || message.author.username;
        onCommand({ type: 'confirm', bookingId: booking.id, slot, staffName });
        return `Locked in choice #${choice}: **${formatSlot(slot.start)}** (${SHOP_TIMEZONE}). The customer has been told.`;
      }

      case 'status': {
        const next = (args[0] || '').toLowerCase();
        if (!STATUSES.includes(next)) return `Status has to be one of: ${STATUSES.join(', ')}.`;
        const staffName = message.member?.displayName || message.author.username;
        onCommand({ type: 'status', bookingId: booking.id, status: next, staffName });
        return `Status is now **${next}**.`;
      }

      case 'info':
        return { embeds: [bookingEmbed(store.getBooking(booking.id))] };

      default:
        return `Unknown command \`!${command}\`. Try \`!help\`.`;
    }
  }

  async function announceBooking(booking) {
    const channel = await client.channels.fetch(bookingsChannelId).catch(() => null);
    if (!channel) {
      console.warn('[discord] bookings channel not reachable:', bookingsChannelId);
      return;
    }

    const roleId = process.env.DISCORD_NOTIFY_ROLE_ID;
    const card = await channel.send({
      content: roleId ? `<@&${roleId}> new booking **${booking.reference}**` : `New booking **${booking.reference}**`,
      embeds: [bookingEmbed(booking)],
    });

    const thread = await openThread(booking, card);
    if (!thread) return;

    store.setThreadId(booking.id, thread.id);
    await thread.send(
      [
        `Chat thread for **${booking.reference}** — ${booking.customer.fullName}.`,
        'Everything you send here reaches them on the website; their replies land back here.',
        'Type `!help` for the thread commands.',
      ].join('\n')
    );
  }

  async function openThread(booking, card) {
    const title = `${booking.reference} · ${booking.customer.fullName}`;
    // Preferred: a thread hanging off the booking card, so the context is right there.
    if (chatChannelId === bookingsChannelId) {
      return card.startThread({ name: title, autoArchiveDuration: 10080 }).catch((err) => {
        console.error('[discord] could not start a thread on the booking card:', err);
        return null;
      });
    }
    const chatChannel = await client.channels.fetch(chatChannelId).catch(() => null);
    if (!chatChannel) {
      console.warn('[discord] chat channel not reachable:', chatChannelId);
      return null;
    }
    return chatChannel.threads
      .create({ name: title, autoArchiveDuration: 10080, type: ChannelType.PublicThread })
      .catch((err) => {
        console.error('[discord] could not create a chat thread:', err);
        return null;
      });
  }

  async function sendToThread(booking, payload) {
    if (!booking?.discordThreadId) return;
    const thread = await client.channels.fetch(booking.discordThreadId).catch(() => null);
    if (!thread) return;
    if (thread.archived) await thread.setArchived(false).catch(() => {});
    await thread.send(payload).catch((err) => console.error('[discord] send failed:', err));
  }

  async function relayCustomerMessage(booking, message) {
    await sendToThread(booking, `**${message.authorName}** (customer): ${message.body}`);
  }

  async function relayStatusChange(booking, text) {
    await sendToThread(booking, `_${text}_`);
  }

  return {
    enabled: true,
    client,
    announceBooking,
    relayCustomerMessage,
    relayStatusChange,
  };
}

export function bookingEmbed(booking) {
  const address = [
    booking.address.line1,
    booking.address.line2,
    `${booking.address.city}, ${booking.address.region} ${booking.address.postalCode}`,
  ]
    .filter(Boolean)
    .join('\n');

  const slots = booking.slots
    .map((s) => `**${s.preference}.** ${formatSlot(s.start)}`)
    .join('\n');

  const addons = booking.service.addons.length ? booking.service.addons.join(', ') : 'none';

  return new EmbedBuilder()
    .setColor(0xffffff)
    .setTitle(`${booking.reference} — ${booking.service.packageLabel}`)
    .setDescription(`${booking.customer.fullName} · ${booking.customer.email} · ${booking.customer.phone}`)
    .addFields(
      {
        name: 'Vehicle',
        value: `${booking.vehicle.year} ${booking.vehicle.make} ${booking.vehicle.name}\n${booking.vehicle.brand} · ${booking.vehicle.size}${
          booking.vehicle.colour ? ` · ${booking.vehicle.colour}` : ''
        }${booking.vehicle.plate ? ` · plate ${booking.vehicle.plate}` : ''}`,
        inline: false,
      },
      { name: 'Address', value: address || '—', inline: true },
      { name: 'Quote', value: `$${booking.service.quotedTotal}\nAdd-ons: ${addons}`, inline: true },
      { name: `Preferred windows (${SHOP_TIMEZONE})`, value: slots || '—', inline: false },
      ...(booking.address.notes ? [{ name: 'Access notes', value: booking.address.notes }] : []),
      ...(booking.notes ? [{ name: 'Customer notes', value: booking.notes }] : [])
    )
    .setFooter({ text: `Status: ${booking.status} · booked ${booking.createdAt}` });
}
