import html
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "diagrams"

W, H = 1920, 1240
INK = "#1e1e1e"
SOFT = "#495057"
PAPER = "#fdfdf7"
BLUE = "#a5d8ff"
ORANGE = "#ffd8a8"
GREEN = "#b2f2bb"
PURPLE = "#d0bfff"
YELLOW = "#fff3bf"
FONT = "Chalkboard SE, Bradley Hand, Segoe Print, Comic Sans MS, cursive"

boxes = []
arrows = []
labels = []


def box(bid, x, y, w, h, lines, fill, size=16, dashed=False, stroke=INK):
    boxes.append(
        dict(id=bid, x=x, y=y, w=w, h=h, lines=lines, fill=fill, size=size, dashed=dashed, stroke=stroke)
    )


def label(lid, x, y, lines, size=15, color=INK, anchor="start"):
    labels.append(dict(id=lid, x=x, y=y, lines=lines, size=size, color=color, anchor=anchor))


def arrow(aid, pts, color=INK, dashed=False, label_at=None, label_lines=None, label_size=13, width=2.5):
    arrows.append(dict(id=aid, pts=pts, color=color, dashed=dashed, width=width))
    if label_at and label_lines:
        label(aid + "-label", label_at[0], label_at[1], label_lines, size=label_size, color=SOFT)


label("title", 60, 30, ["HL Copytrader — order flow & engine loop"], size=30)
label(
    "subtitle",
    60,
    76,
    ["one process · one serial loop · one SQLite writer · order path target < 10s · 30s safety net"],
    size=15,
    color=SOFT,
)

box("t1", 340, 140, 470, 86, ["leader userFills over /ws", "WsListener thread · one conn per leader"], ORANGE, 16)
box("t2", 840, 140, 470, 86, ["30s reconcile tick", "TickEvent · safety net"], PURPLE, 16)
box("t3", 1340, 140, 470, 86, ["UI command", "start / pause / param / stop"], PURPLE, 16)

box(
    "queue",
    340,
    290,
    1470,
    76,
    ["Engine in-queue — FillEvent / TickEvent / CommandEvent", "dedup key: task_id:coin"],
    BLUE,
    16,
)

box("engine", 340, 420, 1100, 680, [], "none", dashed=True)
label("engine-label", 365, 436, ["EngineLoop — single thread · serial · sole order placer"], size=15, color=SOFT)

box("s1", 410, 515, 220, 90, ["1 · dequeue", "dedup by task_id:coin"], BLUE, 16)
box("s2", 1130, 515, 220, 90, ["2 · read fresh state", "leader + follower via /info"], BLUE, 16)
box("s3", 1130, 655, 220, 90, ["3 · plan (pure)", "target → Δ → one action"], BLUE, 16)
box("s4", 1130, 795, 220, 90, ["4 · set leverage", "first entry per coin"], BLUE, 16)
box("s5", 1130, 935, 220, 90, ["5 · place IOC", "reduceOnly when reducing"], BLUE, 16)
box("s6", 410, 935, 220, 90, ["6 · write exec_logs", "result · reason · raw"], BLUE, 16)

box(
    "note",
    710,
    650,
    380,
    200,
    [
        "decision order (planner)",
        "T=0 & F≠0 → close |F|, not Δ",
        "|Δ|·mark < $10 → skip",
        "sign flip → close, then open",
        "leader read failure → skip, never flat",
    ],
    YELLOW,
    14,
)

box("hlpanel", 1600, 490, 290, 560, [], "none", dashed=True)
label("hl-title", 1600, 456, ["Hyperliquid"], size=18)
box("hl1", 1620, 515, 250, 86, ["/info", "meta 60s · mids 1s · state 3s"], ORANGE, 15)
box("hl2", 1620, 700, 250, 86, ["/exchange", "EIP-712 · nonce · cloid"], ORANGE, 15)
box("hl3", 1620, 885, 250, 86, ["/ws", "userFills + backfill"], ORANGE, 15)

box(
    "db",
    40,
    790,
    270,
    250,
    [
        "SQLite /data/app.db",
        "WAL · single writer",
        "",
        "wallets · secret_enc",
        "tasks · Decimal TEXT",
        "coin_states · lock/retry",
        "exec_logs · append-only",
    ],
    GREEN,
    15,
)

box(
    "readpath",
    40,
    430,
    270,
    300,
    ["Read path", "not in the loop", "", "Browser → API routes", "→ SQLite reads", "→ /info cache (3s)"],
    BLUE,
    15,
    dashed=True,
)

for i, (lx, col, text) in enumerate(
    [(340, BLUE, "our process"), (500, ORANGE, "Hyperliquid"), (660, GREEN, "storage"), (810, PURPLE, "trigger")]
):
    box(f"legend-{i}", lx, 1142, 14, 14, [], col, 12)
    label(f"legend-{i}-text", lx + 22, 1136, [text], size=15)

label(
    "invariants",
    340,
    1174,
    [
        "invariants · single writer · one order path · Decimal only · failed leader read ≠ flat · reversal = 2 orders"
    ],
    size=14,
    color=SOFT,
)

arrow("a1", [(575, 226), (575, 290)], PURPLE)
arrow("a2", [(1075, 226), (1075, 290)], PURPLE)
arrow("a3", [(1575, 226), (1575, 290)], PURPLE)
arrow("a4", [(575, 366), (575, 515)])
arrow("a5", [(630, 560), (1120, 560)], label_at=(795, 532), label_lines=["read fresh state"])
arrow("a6", [(1240, 605), (1240, 655)])
arrow("a7", [(1240, 745), (1240, 795)])
arrow("a8", [(1240, 885), (1240, 935)])
arrow("a9", [(1120, 980), (630, 980)], label_at=(810, 986), label_lines=["log result"])
arrow(
    "a10",
    [(520, 935), (520, 605)],
    label_at=(532, 742),
    label_lines=["loop · next event", "state is re-read every round"],
    width=3.5,
)
arrow("a11", [(1350, 560), (1600, 558)], ORANGE, label_at=(1455, 530), label_lines=["read"])
arrow("a12", [(1350, 840), (1600, 745)], ORANGE, label_at=(1454, 800), label_lines=["setLeverage"])
arrow("a13", [(1350, 975), (1600, 790)], ORANGE, label_at=(1460, 902), label_lines=["IOC order"])
arrow("a14", [(410, 980), (310, 950)], GREEN, label_at=(346, 928), label_lines=["write"])


def esc(value):
    return html.escape(str(value), quote=True)


def arrow_marker(key, color):
    return (
        f'<marker id="ah-{key}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/></marker>'
    )


def color_key(color):
    return {INK: "ink", ORANGE: "orange", GREEN: "green", PURPLE: "purple"}[color]


def svg_rect_text(b, anchor_middle=True):
    x, y, w, h, size = b["x"], b["y"], b["w"], b["h"], b["size"]
    lines = b["lines"]
    lh = size * 1.3
    if anchor_middle:
        cx = x + w / 2
        cy = y + h / 2
        first = cy - (len(lines) - 1) * lh / 2
        out = [
            f'<text x="{cx:.1f}" y="{first:.1f}" font-family="{FONT}" font-size="{size}" '
            f'fill="{INK}" text-anchor="middle" dominant-baseline="middle">'
        ]
        for i, line in enumerate(lines):
            dy = "0" if i == 0 else f"{lh:.1f}"
            out.append(f'<tspan x="{cx:.1f}" dy="{dy}">{esc(line)}</tspan>')
        out.append("</text>")
        return "".join(out)
    return ""


def svg():
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        "<defs>",
        '<filter id="wobble" filterUnits="userSpaceOnUse" x="0" y="0" width="1920" height="1240">'
        '<feTurbulence type="fractalNoise" baseFrequency="0.018" numOctaves="2" seed="7" result="n"/>'
        '<feDisplacementMap in="SourceGraphic" in2="n" scale="2.4" xChannelSelector="R" yChannelSelector="G"/>'
        "</filter>",
        arrow_marker("ink", INK),
        arrow_marker("orange", ORANGE),
        arrow_marker("green", GREEN),
        arrow_marker("purple", PURPLE),
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="{PAPER}"/>',
    ]

    for b in boxes:
        if b["lines"]:
            dash = ' stroke-dasharray="9 9"' if b["dashed"] else ""
            rx = 12
            parts.append(
                f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" rx="{rx}" '
                f'fill="{b["fill"]}" stroke="{b["stroke"]}" stroke-width="2.5"{dash} filter="url(#wobble)"/>'
            )
            parts.append(svg_rect_text(b))
        else:
            stroke = "" if b["fill"] != "none" else f' stroke="{b["stroke"]}" stroke-dasharray="9 9"'
            fill = "none" if b["fill"] == "none" else b["fill"]
            parts.append(
                f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" rx="12" '
                f'fill="{fill}"{stroke} stroke-width="2.5" filter="url(#wobble)"/>'
            )

    for item in labels:
        lh = item["size"] * 1.35
        anchor = item["anchor"]
        x = item["x"]
        y = item["y"] + item["size"] * 0.9
        out = [
            f'<text x="{x}" y="{y:.1f}" font-family="{FONT}" font-size="{item["size"]}" '
            f'fill="{item["color"]}" text-anchor="{anchor}">'
        ]
        for i, line in enumerate(item["lines"]):
            dy = "0" if i == 0 else f"{lh:.1f}"
            out.append(f'<tspan x="{x}" dy="{dy}">{esc(line)}</tspan>')
        out.append("</text>")
        parts.append("".join(out))

    for a in arrows:
        key = color_key(a["color"])
        d = "M " + " L ".join(f"{p[0]} {p[1]}" for p in a["pts"])
        dash = ' stroke-dasharray="8 8"' if a["dashed"] else ""
        parts.append(
            f'<path d="{d}" fill="none" stroke="{a["color"]}" stroke-width="{a["width"]}" '
            f'marker-end="url(#ah-{key})"{dash} filter="url(#wobble)"/>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def excalidraw():
    elements = []
    seed = 1000
    now = 1730000000000

    def common(eid, etype, x, y, w, h, color, fill="transparent", dashed=False, roundness=None, stroke_width=2):
        nonlocal seed
        seed += 7919
        return {
            "id": eid,
            "type": etype,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "angle": 0,
            "strokeColor": color,
            "backgroundColor": fill,
            "fillStyle": "solid",
            "strokeWidth": stroke_width,
            "strokeStyle": "dashed" if dashed else "solid",
            "roughness": 1,
            "opacity": 100,
            "groupIds": [],
            "frameId": None,
            "roundness": roundness,
            "seed": seed,
            "version": 1,
            "versionNonce": seed + 13,
            "isDeleted": False,
            "boundElements": [],
            "updated": now,
            "link": None,
            "locked": False,
        }

    for b in boxes:
        fill = "transparent" if b["fill"] == "none" else b["fill"]
        elements.append(
            common(b["id"], "rectangle", b["x"], b["y"], b["w"], b["h"], b["stroke"], fill, b["dashed"], {"type": 3})
        )
        if b["lines"]:
            size = b["size"]
            tw = max(len(line) for line in b["lines"]) * size * 0.56
            th = len(b["lines"]) * size * 1.25
            elements.append(
                {
                    **common(
                        b["id"] + "-text",
                        "text",
                        b["x"] + (b["w"] - tw) / 2,
                        b["y"] + (b["h"] - th) / 2,
                        tw,
                        th,
                        INK,
                        "transparent",
                    ),
                    "text": "\n".join(b["lines"]),
                    "originalText": "\n".join(b["lines"]),
                    "fontSize": size,
                    "fontFamily": 1,
                    "textAlign": "center",
                    "verticalAlign": "middle",
                    "containerId": None,
                    "lineHeight": 1.25,
                }
            )

    for item in labels:
        size = item["size"]
        tw = max(len(line) for line in item["lines"]) * size * 0.56
        th = len(item["lines"]) * size * 1.25
        x = item["x"] if item["anchor"] == "start" else item["x"] - tw / 2
        elements.append(
            {
                **common(item["id"], "text", x, item["y"], tw, th, item["color"], "transparent"),
                "text": "\n".join(item["lines"]),
                "originalText": "\n".join(item["lines"]),
                "fontSize": size,
                "fontFamily": 1,
                "textAlign": "left",
                "verticalAlign": "top",
                "containerId": None,
                "lineHeight": 1.25,
            }
        )

    for a in arrows:
        (x0, y0) = a["pts"][0]
        pts = [[p[0] - x0, p[1] - y0] for p in a["pts"]]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        elements.append(
            {
                **common(
                    a["id"],
                    "arrow",
                    x0,
                    y0,
                    max(xs) - min(xs),
                    max(ys) - min(ys),
                    a["color"],
                    "transparent",
                    a["dashed"],
                    {"type": 2},
                    round(a["width"]),
                ),
                "boundElements": None,
                "points": pts,
                "lastCommittedPoint": None,
                "startBinding": None,
                "endBinding": None,
                "startArrowhead": None,
                "endArrowhead": "arrow",
                "elbowed": False,
            }
        )

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {"gridSize": None, "viewBackgroundColor": PAPER, "scrollX": 0, "scrollY": 0, "zoom": {"value": 0.7}},
        "files": {},
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    svg_path = OUT / "architecture.svg"
    excalidraw_path = OUT / "architecture.excalidraw"
    svg_path.write_text(svg(), encoding="utf-8")
    excalidraw_path.write_text(json.dumps(excalidraw(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {svg_path.relative_to(ROOT)}")
    print(f"wrote {excalidraw_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
