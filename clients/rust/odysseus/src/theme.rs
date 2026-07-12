//! Visual theme. Keeps Odysseus's identity — deep dark, cyan accent, the `◆`
//! mark, Fira Code for data — but as a *designed* app: layered surfaces with
//! real elevation, Inter for UI/body text (Fira Code reserved for code and
//! machine data), soft near-white body, cyan as a true accent, generous rhythm.

use egui::{Color32, FontFamily, FontId, Rounding, Stroke, TextStyle};

const fn rgb(r: u8, g: u8, b: u8) -> Color32 {
    Color32::from_rgb(r, g, b)
}

// Surfaces — a real elevation ramp, deepest → lifted.
pub const BG: Color32 = rgb(0x0e, 0x11, 0x16); // app base
pub const PANEL: Color32 = rgb(0x16, 0x1a, 0x21); // sidebars, bars, composer
pub const FIELD_BG: Color32 = rgb(0x1c, 0x21, 0x2b); // inputs, cards, code
pub const HOVER: Color32 = rgb(0x24, 0x2b, 0x37);
pub const BORDER: Color32 = rgb(0x2a, 0x31, 0x3d); // quiet structural hairline

// Text.
pub const FG: Color32 = rgb(0xdc, 0xe4, 0xec); // body / UI (near-white)
pub const MUTED: Color32 = rgb(0x8a, 0x95, 0xa3);
pub const FAINT: Color32 = rgb(0x5c, 0x66, 0x74);

// Accents — cyan is the identity; the rest are status only.
pub const ACCENT: Color32 = rgb(0x6c, 0xcd, 0xef); // brand ◆, user, focus, primary
pub const ACCENT_DIM: Color32 = rgb(0x3d, 0x6b, 0x80);
pub const GREEN: Color32 = rgb(0x5f, 0xe0, 0x9a);
pub const RED: Color32 = rgb(0xec, 0x6a, 0x76);
pub const WARN: Color32 = rgb(0xe5, 0xb5, 0x67);

// Bubbles.
pub const USER_BUBBLE: Color32 = rgb(0x1b, 0x27, 0x30); // faint cyan-tinted lift
pub const AI_BUBBLE: Color32 = rgb(0x17, 0x1c, 0x24);

/// Reading width for the centered chat + composer column.
pub const COLUMN_W: f32 = 760.0;

/// A heading-weight font id (Inter SemiBold) at `size`.
pub fn semibold(size: f32) -> FontId {
    FontId::new(size, FontFamily::Name("semibold".into()))
}

/// Install fonts + visuals. Call once at startup with the egui context.
pub fn apply(ctx: &egui::Context) {
    install_fonts(ctx);

    let mut style = (*ctx.style()).clone();

    use FontFamily::{Monospace, Proportional};
    style.text_styles = [
        (TextStyle::Heading, semibold(19.0)),
        (TextStyle::Body, FontId::new(14.5, Proportional)),
        (TextStyle::Monospace, FontId::new(13.0, Monospace)),
        (TextStyle::Button, FontId::new(14.0, Proportional)),
        (TextStyle::Small, FontId::new(12.0, Proportional)),
    ]
    .into();

    let v = &mut style.visuals;
    v.dark_mode = true;
    v.override_text_color = Some(FG);
    v.panel_fill = BG;
    v.window_fill = PANEL;
    v.window_stroke = Stroke::new(1.0, BORDER);
    v.window_rounding = Rounding::same(12.0);
    v.window_shadow = egui::epaint::Shadow {
        offset: egui::vec2(0.0, 10.0),
        blur: 32.0,
        spread: 0.0,
        color: Color32::from_black_alpha(120),
    };
    v.popup_shadow = egui::epaint::Shadow {
        offset: egui::vec2(0.0, 6.0),
        blur: 18.0,
        spread: 0.0,
        color: Color32::from_black_alpha(110),
    };
    v.extreme_bg_color = FIELD_BG;
    v.faint_bg_color = FIELD_BG;
    v.hyperlink_color = ACCENT;
    v.selection.bg_fill = ACCENT.gamma_multiply(0.28);
    v.selection.stroke = Stroke::new(1.0, ACCENT);

    let r = Rounding::same(9.0);
    let w = &mut v.widgets;
    w.noninteractive.bg_fill = PANEL;
    w.noninteractive.bg_stroke = Stroke::new(1.0, BORDER);
    w.noninteractive.fg_stroke = Stroke::new(1.0, FG);
    w.noninteractive.rounding = r;

    w.inactive.bg_fill = FIELD_BG;
    w.inactive.weak_bg_fill = FIELD_BG;
    w.inactive.bg_stroke = Stroke::new(1.0, BORDER);
    w.inactive.fg_stroke = Stroke::new(1.0, MUTED);
    w.inactive.rounding = r;
    w.inactive.expansion = 0.0;

    w.hovered.bg_fill = HOVER;
    w.hovered.weak_bg_fill = HOVER;
    w.hovered.bg_stroke = Stroke::new(1.0, ACCENT_DIM);
    w.hovered.fg_stroke = Stroke::new(1.0, FG);
    w.hovered.rounding = r;
    w.hovered.expansion = 1.0;

    w.active.bg_fill = ACCENT.gamma_multiply(0.22);
    w.active.weak_bg_fill = ACCENT.gamma_multiply(0.22);
    w.active.bg_stroke = Stroke::new(1.0, ACCENT);
    w.active.fg_stroke = Stroke::new(1.0, FG);
    w.active.rounding = r;
    w.active.expansion = 1.0;

    w.open.bg_fill = FIELD_BG;
    w.open.bg_stroke = Stroke::new(1.0, BORDER);
    w.open.rounding = r;

    let s = &mut style.spacing;
    s.item_spacing = egui::vec2(9.0, 9.0);
    s.button_padding = egui::vec2(12.0, 7.0);
    s.window_margin = egui::Margin::same(14.0);
    s.menu_margin = egui::Margin::same(8.0);
    s.interact_size.y = 30.0;
    s.combo_width = 0.0;

    ctx.set_style(style);
}

fn install_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    let mut load = |name: &str, bytes: &'static [u8]| {
        fonts.font_data.insert(name.to_owned(), egui::FontData::from_static(bytes));
    };
    load("Inter", include_bytes!("../assets/Inter-Regular.ttf"));
    load("InterSemiBold", include_bytes!("../assets/Inter-SemiBold.ttf"));
    load("FiraCode", include_bytes!("../assets/FiraCode.ttf"));

    // Proportional (UI + body) = Inter; Monospace (code/data) = Fira Code.
    // Keep the bundled fallbacks after ours for glyphs they lack (emoji, CJK).
    fonts.families.entry(FontFamily::Proportional).or_default().insert(0, "Inter".to_owned());
    fonts.families.entry(FontFamily::Monospace).or_default().insert(0, "FiraCode".to_owned());
    fonts
        .families
        .insert(FontFamily::Name("semibold".into()), vec!["InterSemiBold".to_owned(), "Inter".to_owned()]);

    ctx.set_fonts(fonts);
}

/// The stream-pulse: `base` whose alpha breathes on a ~1.4s sine, 0.5→1.0.
/// Used only while an `ody.event.v1` run streams — the single motion the app
/// commits to. Caller must `request_repaint` while it's live.
pub fn pulse_color(ctx: &egui::Context, base: Color32) -> Color32 {
    let t = ctx.input(|i| i.time) as f32;
    let s = 0.5 + 0.5 * (t * std::f32::consts::TAU / 1.4).sin();
    base.gamma_multiply(0.5 + 0.5 * s)
}

/// A rounded chat "bubble": `fill` background, quiet border, one squared corner
/// for the tail (assistant tail bottom-left, user tail bottom-right).
pub fn bubble(fill: Color32, tail_left: bool) -> egui::Frame {
    let r = 14.0;
    let rounding = if tail_left {
        Rounding { nw: r, ne: r, sw: 3.0, se: r }
    } else {
        Rounding { nw: r, ne: r, sw: r, se: 3.0 }
    };
    egui::Frame::none()
        .fill(fill)
        .stroke(Stroke::new(1.0, BORDER))
        .rounding(rounding)
        .inner_margin(egui::Margin::symmetric(14.0, 11.0))
}
