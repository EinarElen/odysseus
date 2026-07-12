//! Visual theme mirroring the Odysseus web app: One-Dark charcoal, near-black
//! panels, cyan text, teal borders, red/green accents, Fira Code throughout.

use egui::{Color32, FontFamily, FontId, Rounding, Stroke, TextStyle};

// Palette lifted straight from the web app's `:root` (static/style.css).
pub const BG: Color32 = Color32::from_rgb(0x28, 0x2c, 0x34); // --bg
pub const PANEL: Color32 = Color32::from_rgb(0x11, 0x11, 0x11); // --panel
pub const FG: Color32 = Color32::from_rgb(0x9c, 0xde, 0xf2); // --fg (cyan)
pub const BORDER: Color32 = Color32::from_rgb(0x35, 0x5a, 0x66); // --border (teal)
pub const RED: Color32 = Color32::from_rgb(0xe0, 0x6c, 0x75); // --red
pub const GREEN: Color32 = Color32::from_rgb(0x50, 0xfa, 0x7b); // --green
pub const WARN: Color32 = Color32::from_rgb(0xf0, 0xad, 0x4e); // --warn
pub const MUTED: Color32 = Color32::from_rgb(0x82, 0x89, 0x97); // --hl-comment
pub const USER_BUBBLE: Color32 = Color32::from_rgb(0x30, 0x35, 0x3f); // ~ mix(fg 8%, bg)
pub const AI_BUBBLE: Color32 = PANEL;
pub const FIELD_BG: Color32 = Color32::from_rgb(0x1e, 0x22, 0x28); // --hl-bg

/// Install fonts + visuals. Call once at startup with the egui context.
pub fn apply(ctx: &egui::Context) {
    install_fonts(ctx);

    let mut style = (*ctx.style()).clone();

    // Everything is Fira Code, sized to read like the web app.
    use FontFamily::{Monospace, Proportional};
    style.text_styles = [
        (TextStyle::Heading, FontId::new(19.0, Proportional)),
        (TextStyle::Body, FontId::new(14.5, Proportional)),
        (TextStyle::Monospace, FontId::new(13.5, Monospace)),
        (TextStyle::Button, FontId::new(14.0, Proportional)),
        (TextStyle::Small, FontId::new(11.5, Proportional)),
    ]
    .into();

    let v = &mut style.visuals;
    v.dark_mode = true;
    v.override_text_color = Some(FG);
    v.panel_fill = BG;
    v.window_fill = PANEL;
    v.window_stroke = Stroke::new(1.0, BORDER);
    v.window_rounding = Rounding::same(10.0);
    v.extreme_bg_color = FIELD_BG; // text-edit background
    v.faint_bg_color = Color32::from_rgb(0x20, 0x24, 0x2b);
    v.hyperlink_color = FG;
    v.selection.bg_fill = RED.linear_multiply(0.35);
    v.selection.stroke = Stroke::new(1.0, RED);

    // Widgets: charcoal fills, teal hairline borders, red on interaction.
    let r = Rounding::same(7.0);
    v.widgets.noninteractive.bg_fill = PANEL;
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, BORDER.linear_multiply(0.6));
    v.widgets.noninteractive.fg_stroke = Stroke::new(1.0, FG);
    v.widgets.noninteractive.rounding = r;

    v.widgets.inactive.bg_fill = Color32::from_rgb(0x22, 0x27, 0x30);
    v.widgets.inactive.weak_bg_fill = Color32::from_rgb(0x22, 0x27, 0x30);
    v.widgets.inactive.bg_stroke = Stroke::new(1.0, BORDER);
    v.widgets.inactive.fg_stroke = Stroke::new(1.0, FG);
    v.widgets.inactive.rounding = r;

    v.widgets.hovered.bg_fill = Color32::from_rgb(0x2c, 0x32, 0x3d);
    v.widgets.hovered.weak_bg_fill = Color32::from_rgb(0x2c, 0x32, 0x3d);
    v.widgets.hovered.bg_stroke = Stroke::new(1.0, RED);
    v.widgets.hovered.fg_stroke = Stroke::new(1.0, FG);
    v.widgets.hovered.rounding = r;

    v.widgets.active.bg_fill = RED.linear_multiply(0.25);
    v.widgets.active.weak_bg_fill = RED.linear_multiply(0.25);
    v.widgets.active.bg_stroke = Stroke::new(1.0, RED);
    v.widgets.active.fg_stroke = Stroke::new(1.0, FG);
    v.widgets.active.rounding = r;

    v.widgets.open.bg_fill = FIELD_BG;
    v.widgets.open.bg_stroke = Stroke::new(1.0, BORDER);

    style.spacing.item_spacing = egui::vec2(8.0, 8.0);
    style.spacing.button_padding = egui::vec2(10.0, 5.0);
    style.spacing.window_margin = egui::Margin::same(12.0);

    ctx.set_style(style);
}

fn install_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    fonts.font_data.insert(
        "FiraCode".to_owned(),
        egui::FontData::from_static(include_bytes!("../assets/FiraCode.ttf")),
    );
    // Make Fira Code the primary face for both families so the whole UI is mono,
    // exactly like the web app; keep the bundled fallbacks after it for glyphs
    // Fira Code lacks (emoji, CJK).
    for family in [FontFamily::Proportional, FontFamily::Monospace] {
        fonts.families.entry(family).or_default().insert(0, "FiraCode".to_owned());
    }
    ctx.set_fonts(fonts);
}

/// A rounded "bubble" frame in the web app's style: `fill` background, teal
/// hairline border, generous padding. `tail` picks which corner is squared off.
pub fn bubble(fill: Color32, tail_left: bool) -> egui::Frame {
    let r = 16.0;
    let rounding = if tail_left {
        Rounding { nw: r, ne: r, sw: 0.0, se: r } // AI: tail bottom-left
    } else {
        Rounding { nw: r, ne: r, sw: r, se: 0.0 } // user: tail bottom-right
    };
    egui::Frame::none()
        .fill(fill)
        .stroke(Stroke::new(1.0, BORDER))
        .rounding(rounding)
        .inner_margin(egui::Margin::symmetric(12.0, 9.0))
}
