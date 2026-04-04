#!/usr/bin/env node

const fs = require("node:fs/promises");
const path = require("node:path");

const SOURCE_URL =
  "https://www.battlespirits.com/cardlist/index.php?search=true&freewords=26R";
const SOURCE_ORIGIN = "https://www.battlespirits.com";
const TARGET_IDS = process.env.CARD_IDS
  ? new Set(
      process.env.CARD_IDS.split(",")
        .map((id) => id.trim())
        .filter(Boolean)
    )
  : null;
const CARDS_JSON_PATH = path.resolve(process.cwd(), "BattleSpiritsCards.json");
const IMAGES_ROOT = path.resolve(process.cwd(), "images");
const PUBLIC_IMAGE_ROOT =
  "https://balbi.github.io/TCG-Arena-Battle-Spirits/images";

const COLOR_MAP = {
  赤: "Red",
  紫: "Purple",
  緑: "Green",
  白: "White",
  黄: "Yellow",
  青: "Blue",
  多色: "Multicolor",
};

const TYPE_MAP = {
  スピリット: "Spirit",
  Spirit: "Spirit",
  ネクサス: "Nexus",
  Nexus: "Nexus",
  マジック: "Magic",
  Magic: "Magic",
};

function decodeHtml(value) {
  return value
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .trim();
}

function stripTags(value) {
  return decodeHtml(value.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim());
}

function mapColor(value) {
  return COLOR_MAP[value] || value;
}

function normalizeType(value) {
  const mapped = TYPE_MAP[value];
  if (!mapped) {
    throw new Error(
      `Unsupported card type/category '${value}'. Expected Spirit, Nexus, or Magic.`
    );
  }
  return mapped;
}

function extractTagContent(block, regex) {
  const match = regex.exec(block);
  return match ? stripTags(match[1]) : "";
}

function extractFirstThumbnailImage(listItemBlock) {
  const thumbMatch = /<div class="thumbnail">([\s\S]*?)<\/div>/.exec(listItemBlock);
  if (!thumbMatch) return "";

  const imgTag = /<img[^>]*>/.exec(thumbMatch[1]);
  if (!imgTag) return "";

  const dataSrc = /data-src="([^"]+)"/.exec(imgTag[0]);
  if (dataSrc) return dataSrc[1];

  const src = /src="([^"]+)"/.exec(imgTag[0]);
  return src ? src[1] : "";
}

function parseCardFromLi(liBlock) {
  const listItemMatch = /<div class="listItem"[\s\S]*?>([\s\S]*?)<\/div>\s*<\/a>/.exec(
    liBlock
  );
  if (!listItemMatch) return null;

  const listItem = listItemMatch[1];
  const id = extractTagContent(
    listItem,
    /<p class="number">[\s\S]*?<span class="num">([\s\S]*?)<\/span>/
  );
  if (!id) return null;

  const name = extractTagContent(listItem, /<h3 class="name">([\s\S]*?)<\/h3>/);
  const costText = extractTagContent(
    listItem,
    /<dd class="costVal">([\s\S]*?)<\/dd>/
  );
  const cost = Number.parseInt(costText, 10);

  const typeInfo = /<dd class="type">([\s\S]*?)<\/dd>/.exec(listItem)?.[1] || "";
  const colorRaw = extractTagContent(typeInfo, /<span class="attribute">([\s\S]*?)<\/span>/);
  const category = extractTagContent(typeInfo, /<span class="category">([\s\S]*?)<\/span>/);
  const systemRaw = extractTagContent(typeInfo, /<span class="system">([\s\S]*?)<\/span>/);
  const family = systemRaw
    .split(/[\/,、]+/u)
    .map((v) => v.trim())
    .filter(Boolean);

  const alleviationInner =
    /<dd class="alleviationVal">([\s\S]*?)<\/dd>/.exec(listItem)?.[1] || "";
  // Intentionally preserves duplicates (e.g. two red symbols -> ["Red", "Red"]).
  const reductions = [...alleviationInner.matchAll(/<img[^>]*alt="([^"]+)"/g)].map(
    (m) => mapColor(stripTags(m[1]))
  );

  const imagePath = extractFirstThumbnailImage(listItem);

  return {
    id,
    name,
    cost: Number.isFinite(cost) ? cost : 0,
    type: normalizeType(category),
    color: colorRaw ? [mapColor(colorRaw)] : [],
    reductions,
    family,
    imagePath,
  };
}

function dedupeCardsByIdPreferNonPromo(cards) {
  const byId = new Map();
  for (const card of cards) {
    const existing = byId.get(card.id);
    if (!existing) {
      byId.set(card.id, card);
      continue;
    }

    const existingPromo = /_promo\./.test(existing.imagePath);
    const currentPromo = /_promo\./.test(card.imagePath);
    if (existingPromo && !currentPromo) {
      byId.set(card.id, card);
    }
  }

  return [...byId.values()];
}

function buildCardRecord(parsedCard, imagePublicUrl) {
  const setCode = parsedCard.id.split("-")[0];
  return {
    id: parsedCard.id,
    face: {
      front: {
        name: parsedCard.name,
        type: parsedCard.type,
        cost: parsedCard.cost,
        image: imagePublicUrl,
        isHorizontal: false,
      },
    },
    name: parsedCard.name,
    type: parsedCard.type,
    cost: parsedCard.cost,
    Color: parsedCard.color,
    Reduction: parsedCard.reductions,
    Family: parsedCard.family,
    Set: [setCode],
    isToken: false,
  };
}

function mergeCard(existingCard, newCard) {
  return {
    ...existingCard,
    ...newCard,
    face: {
      ...(existingCard?.face || {}),
      ...(newCard.face || {}),
      front: {
        ...(existingCard?.face?.front || {}),
        ...(newCard.face?.front || {}),
      },
    },
  };
}

async function fetchHtml(url) {
  const response = await fetch(url, {
    headers: {
      "user-agent": "Mozilla/5.0 (Node.js scraper)",
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to fetch page: ${response.status} ${response.statusText}`);
  }
  return response.text();
}

function resolveImageUrl(imagePath) {
  if (!imagePath) return "";
  return new URL(imagePath, SOURCE_ORIGIN).toString();
}

async function downloadImage(imageUrl, outputPath) {
  const response = await fetch(imageUrl, {
    headers: {
      "user-agent": "Mozilla/5.0 (Node.js scraper)",
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to download image: ${response.status} ${response.statusText}`);
  }
  const arrayBuffer = await response.arrayBuffer();
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, Buffer.from(arrayBuffer));
}

function extractLiBlocksFromViewSwitch(html) {
  const ulMatch = /<ul[^>]*id="viewSwitch"[^>]*>([\s\S]*?)<\/ul>/.exec(html);
  if (!ulMatch) return [];
  return ulMatch[1].match(/<li\b[\s\S]*?<\/li>/g) || [];
}

async function main() {
  const html = await fetchHtml(SOURCE_URL);
  const liBlocks = extractLiBlocksFromViewSwitch(html);
  if (liBlocks.length === 0) {
    throw new Error('No cards found under <ul id="viewSwitch">.');
  }

  const parsedCards = dedupeCardsByIdPreferNonPromo(
    liBlocks
      .map(parseCardFromLi)
      .filter(Boolean)
      .filter((card) => (TARGET_IDS ? TARGET_IDS.has(card.id) : true))
  );

  if (parsedCards.length === 0) {
    throw new Error(
      TARGET_IDS
        ? "Target card ID(s) were not found on the page."
        : "No cards were parsed from the page."
    );
  }

  const cardsJsonRaw = await fs.readFile(CARDS_JSON_PATH, "utf8");
  const cardsJson = JSON.parse(cardsJsonRaw);

  for (const parsedCard of parsedCards) {
    const setCode = parsedCard.id.split("-")[0];
    const imageUrl = resolveImageUrl(parsedCard.imagePath);
    const imageUrlNoQuery = imageUrl.split("?")[0];
    const ext = path.extname(new URL(imageUrlNoQuery).pathname) || ".webp";
    const imageFileName = `${parsedCard.id}${ext}`;
    const imageOutputPath = path.join(IMAGES_ROOT, setCode, imageFileName);
    const imagePublicUrl = `${PUBLIC_IMAGE_ROOT}/${setCode}/${imageFileName}`;

    const existing = cardsJson[parsedCard.id] || {};
    const updatedCard = buildCardRecord(parsedCard, imagePublicUrl);
    cardsJson[parsedCard.id] = mergeCard(existing, updatedCard);

    if (imageUrl) {
      await downloadImage(imageUrl, imageOutputPath);
      console.log(`Downloaded image: ${imageOutputPath}`);
    }

    console.log(`Upserted card: ${parsedCard.id} (${parsedCard.name})`);
  }

  await fs.writeFile(CARDS_JSON_PATH, `${JSON.stringify(cardsJson, null, 2)}\n`, "utf8");
  console.log(`Updated cards JSON: ${CARDS_JSON_PATH}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
