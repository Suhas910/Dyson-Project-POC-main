import {
  Dialog,
  DialogContent,
  DialogTitle,
  Box,
  Chip,
  Typography,
  IconButton,
  Divider,
  Tooltip,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import StraightenIcon from "@mui/icons-material/Straighten";
import PlaceOutlinedIcon from "@mui/icons-material/PlaceOutlined";
import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";

import { statusToken, SEVERITY_TOKENS } from "../theme";

// What each severity actually means for the person reading it. The word alone
// ("major") is a label; this is the decision it implies.
const SEVERITY_MEANING = {
  critical:
    "The part cannot be made as drawn, or will fail in a way that scraps it. " +
    "This has to change before tooling.",
  major:
    "The part can be made, but expect defects, rework or a tooling compromise. " +
    "Worth fixing while changes are still cheap.",
  minor:
    "A refinement rather than a defect. Worth doing if the design is open, not " +
    "worth reopening it for.",
};

const UNIT_SUFFIX = {
  degrees: "°",
  deg: "°",
  mm: " mm",
  ratio: "×",
};

function suffixFor(units) {
  return UNIT_SUFFIX[units] || (units ? ` ${units}` : "");
}

/** Pulls the leading number out of a formatted measurement like "0.820 mm". */
function measuredValue(measured) {
  if (!measured) return null;
  const match = /-?\d+(?:\.\d+)?/.exec(measured);
  return match ? Number(match[0]) : null;
}

/** The engine's reasons are sentence fragments; they need a capital to follow one. */
function asSentence(text) {
  if (!text) return "";
  const trimmed = text.trim();
  const capitalised = trimmed[0].toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capitalised) ? capitalised : `${capitalised}.`;
}

function round(value) {
  // Three decimals matches how measurements are formatted elsewhere; trailing
  // zeros are dropped so "0.5" does not become "0.500" in prose.
  return Number(value.toFixed(3)).toString();
}

/**
 * The rule's requirement in plain words, plus the bounds for comparing against.
 * Returns null for a rule with no numeric predicate — a qualitative rule has
 * nothing to fall short of.
 */
function describeRequirement(rule) {
  const predicate = rule?.predicate;
  if (!predicate) return null;

  const unit = suffixFor(rule.units);
  const { type, operator, threshold } = predicate;

  if (type === "range" || predicate.min != null || predicate.max != null) {
    const { min, max } = predicate;
    if (min != null && max != null) {
      return { text: `between ${round(min)}${unit} and ${round(max)}${unit}`, min, max, unit };
    }
    if (min != null) return { text: `at least ${round(min)}${unit}`, min, max: null, unit };
    if (max != null) return { text: `no more than ${round(max)}${unit}`, min: null, max, unit };
    return null;
  }

  if (threshold == null) return null;
  const phrasing = {
    ">=": ["at least", threshold, null],
    ">": ["more than", threshold, null],
    "<=": ["no more than", null, threshold],
    "<": ["less than", null, threshold],
    "==": ["exactly", threshold, threshold],
  }[operator];
  if (!phrasing) return null;

  const [words, min, max] = phrasing;
  return { text: `${words} ${round(threshold)}${unit}`, min, max, unit };
}

/**
 * The one sentence the reader came for: what was required, what was measured,
 * and by how much it missed. Everything else in this dialog is context.
 */
function verdictSentence(finding, requirement) {
  const value = measuredValue(finding.measured);
  const status = finding.status;

  if (status === "NOT_EVALUATED") {
    return asSentence(
      finding.reason ||
        "This rule was not evaluated on this part, and no reason was recorded"
    );
  }

  if (status === "ERROR") {
    return (
      finding.reason ||
      "Evaluation was attempted and failed. The result is not a verdict either way."
    );
  }

  if (!requirement || value == null) {
    // A qualitative rule, or one whose measurement is not a single number.
    if (status === "NEEDS_REVIEW") {
      return (
        finding.reason ||
        "This rule calls for engineering judgement rather than a measurement, so " +
          "the engine did not decide it."
      );
    }
    return finding.reason || "No numeric comparison was recorded for this finding.";
  }

  const { min, max, unit } = requirement;
  const measured = `${round(value)}${unit}`;

  // An advisory rule yields NEEDS_REVIEW precisely because its numbers are a
  // recommended range rather than a limit. Calling a miss "short of the limit"
  // would contradict the engine's own note further down the same dialog.
  const advisory = status === "NEEDS_REVIEW";
  const outsideBy = (amount, direction) =>
    advisory
      ? `The rule recommends ${requirement.text}. This measures ${measured}, ${round(
          amount
        )}${unit} ${direction} that range. ${asSentence(
          finding.reason ||
            "It is recommended practice rather than a hard limit, so this is flagged for review rather than failed"
        )}`
      : `The rule ${direction === "below" ? "requires" : "allows"} ${
          requirement.text
        }. This measures ${measured} — ${round(amount)}${unit} ${
          direction === "below" ? "short of" : "over"
        } the limit.`;

  if (min != null && value < min) return outsideBy(min - value, "below");
  if (max != null && value > max) return outsideBy(value - max, "above");

  // Inside the band. For a passing finding, say how much room there is; for a
  // NEEDS_REVIEW one, the reason explains why it was not simply passed.
  const margins = [];
  if (min != null) margins.push(`${round(value - min)}${unit} above the minimum`);
  if (max != null) margins.push(`${round(max - value)}${unit} below the maximum`);

  if (status === "NEEDS_REVIEW") {
    return `The rule asks for ${requirement.text}, and this measures ${measured}${
      margins.length ? ` (${margins.join(", ")})` : ""
    }. ${asSentence(finding.reason || "It was left for review rather than decided")}`;
  }

  return `The rule requires ${requirement.text}. This measures ${measured}${
    margins.length ? ` — ${margins.join(", ")}` : ""
  }.`;
}

function Section({ icon, title, children }) {
  return (
    <Box sx={{ display: "grid", gap: 0.75 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        {icon}
        <Typography
          variant="overline"
          sx={{ letterSpacing: "0.08em", color: "text.secondary", lineHeight: 1.4 }}
        >
          {title}
        </Typography>
      </Box>
      {children}
    </Box>
  );
}

function FindingDetail({ finding, rule, open, onClose }) {
  if (!finding) return null;

  const status = statusToken(finding.status);
  const severity = finding.severity
    ? SEVERITY_TOKENS[finding.severity] || SEVERITY_TOKENS.minor
    : null;
  const requirement = describeRequirement(rule);
  const verdict = verdictSentence(finding, requirement);

  // The reason is already folded into the verdict sentence for these states;
  // repeating it below would read as two different explanations of one thing.
  const reasonShownAbove =
    ["NOT_EVALUATED", "ERROR"].includes(finding.status) ||
    Boolean(verdict && finding.reason && verdict.includes(finding.reason));

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth scroll="paper">
      <DialogTitle sx={{ pr: 6, pb: 1.5 }}>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mb: 1 }}>
          <Chip
            size="small"
            label={finding.rule_id}
            sx={{ fontFamily: "monospace", fontWeight: 700 }}
          />
          <Chip
            size="small"
            label={status.label}
            sx={{
              backgroundColor: status.tint,
              color: status.text || status.main,
              border: `1px solid ${status.border}`,
              fontWeight: 700,
            }}
          />
          {severity && (
            <Tooltip title={SEVERITY_MEANING[finding.severity] || ""}>
              <Chip
                size="small"
                label={finding.severity}
                sx={{
                  backgroundColor: severity.tint,
                  color: severity.text || severity.main,
                  border: `1px solid ${severity.border}`,
                  fontWeight: 700,
                }}
              />
            </Tooltip>
          )}
          {finding.process_family && (
            <Chip size="small" variant="outlined" label={finding.process_family} />
          )}
        </Box>
        <Typography variant="h6" sx={{ lineHeight: 1.3 }}>
          {finding.rule_name}
        </Typography>
        <IconButton
          onClick={onClose}
          aria-label="Close"
          sx={{ position: "absolute", right: 8, top: 8 }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent dividers sx={{ display: "grid", gap: 2.5, pt: 2.5 }}>
        {/* The verdict leads, tinted by status, because it is the question the
            reader clicked to answer. */}
        <Box
          sx={{
            p: 2,
            borderRadius: 1.5,
            backgroundColor: status.tint,
            borderLeft: `4px solid ${status.main}`,
          }}
        >
          <Typography variant="body1" sx={{ lineHeight: 1.6 }}>
            {verdict}
          </Typography>
        </Box>

        {finding.location && finding.location !== "part" && (
          <Section
            icon={<PlaceOutlinedIcon fontSize="small" sx={{ color: "text.secondary" }} />}
            title="Where"
          >
            <Typography variant="body2">
              {finding.feature_label || finding.location}
              {finding.feature_label && (
                <Typography component="span" variant="body2" color="text.secondary">
                  {" "}
                  · {finding.location}
                </Typography>
              )}
              {finding.measurement_point && (
                <Typography
                  variant="body2"
                  sx={{ fontFamily: "monospace", color: "text.secondary", mt: 0.5 }}
                >
                  At ({finding.measurement_point[0]}, {finding.measurement_point[1]},{" "}
                  {finding.measurement_point[2]})
                </Typography>
              )}
            </Typography>
          </Section>
        )}

        {finding.location === "part" && (
          <Section
            icon={<PlaceOutlinedIcon fontSize="small" sx={{ color: "text.secondary" }} />}
            title="Where"
          >
            <Typography variant="body2" color="text.secondary">
              The whole part — this rule is not about one face.
            </Typography>
          </Section>
        )}

        {rule?.description && (
          <Section
            icon={<StraightenIcon fontSize="small" sx={{ color: "text.secondary" }} />}
            title="What this rule checks"
          >
            <Typography variant="body2" sx={{ lineHeight: 1.6 }}>
              {rule.description}
            </Typography>
          </Section>
        )}

        {severity && (
          <Section
            icon={<StraightenIcon fontSize="small" sx={{ color: "text.secondary" }} />}
            title={`What "${finding.severity}" means here`}
          >
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
              {SEVERITY_MEANING[finding.severity]}
            </Typography>
          </Section>
        )}

        {finding.reason && !reasonShownAbove && (
          <Section
            icon={<StraightenIcon fontSize="small" sx={{ color: "text.secondary" }} />}
            title="Note from the engine"
          >
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
              {finding.reason}
            </Typography>
          </Section>
        )}

        {finding.agent_commentary && (
          <>
            <Divider />
            <Section
              icon={<AutoAwesomeIcon fontSize="small" sx={{ color: "primary.main" }} />}
              title="AI commentary"
            >
              <Typography variant="body2" sx={{ lineHeight: 1.65 }}>
                {finding.agent_commentary}
              </Typography>
              {/* Attribution matters: everything above this line is measured,
                  everything in this block was written by a model that cannot
                  change the verdict. */}
              <Typography variant="caption" color="text.secondary">
                Written by a language model from the measurements above. It cannot
                change the verdict.
                {finding.agent_confidence != null &&
                  ` Its stated confidence: ${(finding.agent_confidence * 100).toFixed(
                    0
                  )}%.`}
              </Typography>
            </Section>
          </>
        )}

        {(finding.guideline_ref || rule?.provenance) && (
          <Section
            icon={<MenuBookOutlinedIcon fontSize="small" sx={{ color: "text.secondary" }} />}
            title="Where this rule comes from"
          >
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
              {finding.guideline_ref || rule.guideline_ref}
            </Typography>
          </Section>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default FindingDetail;
