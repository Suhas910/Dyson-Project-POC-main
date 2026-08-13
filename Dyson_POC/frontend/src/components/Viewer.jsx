import {
  Box,
  Card,
  CardContent,
  Chip,
  Divider,
  Typography,
} from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import HourglassBottomIcon from "@mui/icons-material/HourglassBottom";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import RuleIcon from "@mui/icons-material/Rule";

import { STATUS_TOKENS } from "../theme";

function getRuleStatus(ruleId, findings) {
  if (!findings || findings.length === 0) return null;

  const ruleFindings = findings.filter((f) => f.rule_id === ruleId);
  if (ruleFindings.length === 0) return null;

  const hasNonCompliant = ruleFindings.some(
    (f) => f.status === "NON-COMPLIANT",
  );
  const hasReview = ruleFindings.some((f) => f.status === "NEEDS_REVIEW");
  const allCompliant = ruleFindings.every((f) => f.status === "COMPLIANT");
  const allUnevaluated = ruleFindings.every((f) => f.status === "NOT_EVALUATED");

  if (hasNonCompliant) return "NON-COMPLIANT";
  if (hasReview) return "NEEDS_REVIEW";
  if (allCompliant) return "COMPLIANT";
  if (allUnevaluated) return "NOT_EVALUATED";
  return null;
}

function getRuleStats(ruleId, findings) {
  if (!findings || findings.length === 0) return null;

  const ruleFindings = findings.filter((f) => f.rule_id === ruleId);
  if (ruleFindings.length === 0) return null;

  return {
    total: ruleFindings.length,
    compliant: ruleFindings.filter((f) => f.status === "COMPLIANT").length,
    nonCompliant: ruleFindings.filter((f) => f.status === "NON-COMPLIANT")
      .length,
    review: ruleFindings.filter((f) => f.status === "NEEDS_REVIEW").length,
    notEvaluated: ruleFindings.filter((f) => f.status === "NOT_EVALUATED")
      .length,
  };
}

function StatusIcon({ status }) {
  const token = STATUS_TOKENS[status];
  if (!token) return null;

  const Icon = {
    COMPLIANT: CheckCircleIcon,
    "NON-COMPLIANT": CancelIcon,
    NEEDS_REVIEW: HourglassBottomIcon,
    NOT_EVALUATED: HelpOutlineIcon,
  }[status];

  return Icon ? <Icon sx={{ color: token.text, fontSize: 20 }} /> : null;
}

function formatPredicate(predicate, units) {
  if (!predicate) return "";

  const unitStr = units ? ` ${units}` : "";

  if (predicate.type === "range") {
    return `Between ${predicate.min}${unitStr} and ${predicate.max}${unitStr}`;
  }

  if (predicate.type === "simple" || predicate.operator) {
    const operator = predicate.operator || "?";
    const threshold = predicate.threshold ?? "?";
    return `Value ${operator} ${threshold}${unitStr}`;
  }

  return "Complex predicate";
}

function Viewer({ findings, rules }) {
  // If no rules are passed from the parent, there's nothing to display.
  // This happens on initial load before an analysis is run.
  const rulesToDisplay = rules || [];

  return (
    <Box sx={{ p: 2, height: "100%", overflow: "auto" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2 }}>
        <RuleIcon color="primary" />
        <Typography variant="h6">DFM Rules Catalog</Typography>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Rules applied during analysis against each face of the part model.
      </Typography>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {rulesToDisplay.map((rule) => {
          const status = getRuleStatus(rule.rule_id, findings);
          const stats = getRuleStats(rule.rule_id, findings);

          return (
            <Card key={rule.rule_id} variant="outlined">
              <CardContent sx={{ p: 2, "&:last-child": { pb: 2 } }}>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    mb: 1,
                  }}
                >
                  <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                    <Chip
                      label={rule.rule_id}
                      size="small"
                      sx={{ fontWeight: 700, fontFamily: "monospace" }}
                    />
                    <Typography variant="subtitle2" fontWeight={600}>
                      {rule.rule_name}
                    </Typography>
                  </Box>
                  {status && <StatusIcon status={status} />}
                </Box>

                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontFamily: "monospace", display: "block", mb: 1 }}
                >
                  {rule.guideline_ref}
                </Typography>

                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mb: 1.5 }}
                >
                  {rule.description}
                </Typography>

                {rule.predicate && (
                  <Box sx={{ mb: 1 }}>
                    <Chip
                      label={`Check: ${formatPredicate(rule.predicate, rule.units)}`}
                      size="small"
                      variant="outlined"
                      color="info"
                    />
                  </Box>
                )}

                <Chip
                  label={
                    rule.kind === "quantitative"
                      ? "Quantitative"
                      : rule.kind === "qualitative"
                        ? "Qualitative (AI Review)"
                        : "Not Yet Computable"
                  }
                  size="small"
                  sx={{
                    fontSize: "0.7rem",
                    backgroundColor:
                      rule.kind === "quantitative"
                        ? "rgba(26,35,126,0.08)"
                        : rule.kind === "qualitative"
                          ? "rgba(237,108,2,0.08)"
                          : "rgba(158,158,158,0.12)",
                    color:
                      rule.kind === "quantitative"
                        ? "primary.dark"
                        : rule.kind === "qualitative"
                          ? "warning.dark"
                          : "text.secondary",
                  }}
                />

                {stats && (
                  <>
                    <Divider sx={{ my: 1.5 }} />
                    <Box
                      sx={{
                        display: "flex",
                        gap: 1.5,
                        flexWrap: "wrap",
                      }}
                    >
                      <Typography variant="caption" color="text.secondary">
                        <strong>{stats.total}</strong>{" "}
                        {stats.total === 1 ? "finding" : "findings"}
                      </Typography>
                      {stats.compliant > 0 && (
                        <Typography
                          variant="caption"
                          sx={{ color: STATUS_TOKENS.COMPLIANT.text }}
                        >
                          <strong>{stats.compliant}</strong> compliant
                        </Typography>
                      )}
                      {stats.nonCompliant > 0 && (
                        <Typography
                          variant="caption"
                          sx={{ color: STATUS_TOKENS["NON-COMPLIANT"].text }}
                        >
                          <strong>{stats.nonCompliant}</strong> non-compliant
                        </Typography>
                      )}
                      {stats.review > 0 && (
                        <Typography
                          variant="caption"
                          sx={{ color: STATUS_TOKENS.NEEDS_REVIEW.text }}
                        >
                          <strong>{stats.review}</strong> to review
                        </Typography>
                      )}
                      {stats.notEvaluated > 0 && (
                        <Typography
                          variant="caption"
                          sx={{ color: STATUS_TOKENS.NOT_EVALUATED.text }}
                        >
                          <strong>{stats.notEvaluated}</strong> not evaluated
                        </Typography>
                      )}
                    </Box>
                  </>
                )}
              </CardContent>
            </Card>
          );
        })}
      </Box>
    </Box>
  );
}

export default Viewer;
