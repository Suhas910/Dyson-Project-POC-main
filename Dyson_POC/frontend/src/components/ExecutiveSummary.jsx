import { Box, Card, CardContent, Chip, Divider, Typography } from "@mui/material";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";

import { SEVERITY_TOKENS } from "../theme";

/**
 * The part-level narrative that opens the report.
 *
 * Carries the brand violet rather than a status colour: it interprets the
 * findings, it does not add a verdict of its own, and colouring it red or green
 * would imply otherwise.
 *
 * Model-written text is always labelled as such. An engineer reading a
 * manufacturing recommendation needs to know whether it came from a measurement
 * or from a language model, and the panel is worse than useless if it blurs
 * that line.
 */
function ExecutiveSummary({ summary, llm }) {
  // No model configured, or the request failed: say so plainly rather than
  // rendering an empty panel that looks like a loading state.
  if (!summary) {
    if (llm && llm.enabled === false) {
      return (
        <Card sx={{ mb: 2, borderStyle: "dashed" }}>
          <CardContent
            sx={{ display: "flex", alignItems: "center", gap: 1.5, py: 1.5 }}
          >
            <InfoOutlinedIcon sx={{ color: "text.secondary" }} />
            <Typography variant="body2" color="text.secondary">
              AI summary unavailable — no model is configured. Deterministic
              results below are unaffected.
            </Typography>
          </CardContent>
        </Card>
      );
    }
    return null;
  }

  const { headline, assessment, key_risks: keyRisks = [], coverage_note: coverageNote } =
    summary;

  return (
    <Card
      sx={{
        mb: 2,
        position: "relative",
        overflow: "hidden",
        "&::before": {
          content: '""',
          position: "absolute",
          insetInline: 0,
          top: 0,
          height: 3,
          backgroundColor: "primary.main",
        },
      }}
    >
      <CardContent sx={{ p: 2.5 }}>
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5, flexWrap: "wrap" }}
        >
          <AutoAwesomeIcon sx={{ color: "primary.main", fontSize: 20 }} />
          <Typography variant="subtitle2" sx={{ color: "primary.main", fontWeight: 700 }}>
            Engineering Summary
          </Typography>
          {llm?.model && (
            <Chip
              label={`AI-generated · ${llm.model}`}
              size="small"
              sx={{
                backgroundColor: "brand.violetTint",
                color: "primary.main",
                fontWeight: 600,
                fontSize: "0.7rem",
              }}
            />
          )}
        </Box>

        {headline && (
          <Typography variant="h6" sx={{ mb: 1, lineHeight: 1.4 }}>
            {headline}
          </Typography>
        )}

        {assessment && (
          <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.7 }}>
            {assessment}
          </Typography>
        )}

        {keyRisks.length > 0 && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}
            >
              Priority risks
            </Typography>
            <Box
              component="ol"
              sx={{ listStyle: "none", m: 0, mt: 1.5, p: 0, display: "grid", gap: 1.5 }}
            >
              {keyRisks.map((risk, index) => {
                const token =
                  SEVERITY_TOKENS[risk.severity] || SEVERITY_TOKENS.minor;
                return (
                  <Box
                    component="li"
                    key={`${risk.title}-${index}`}
                    sx={{
                      pl: 1.5,
                      borderLeft: `3px solid ${token.main}`,
                    }}
                  >
                    <Box
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        mb: 0.25,
                        flexWrap: "wrap",
                      }}
                    >
                      <Typography variant="body2" sx={{ fontWeight: 700 }}>
                        {risk.title}
                      </Typography>
                      {risk.severity && (
                        <Chip
                          label={risk.severity}
                          size="small"
                          sx={{
                            backgroundColor: token.tint,
                            color: token.text,
                            border: `1px solid ${token.border}`,
                            fontWeight: 600,
                            height: 20,
                            fontSize: "0.7rem",
                          }}
                        />
                      )}
                    </Box>
                    {risk.why_it_matters && (
                      <Typography variant="body2" color="text.secondary">
                        {risk.why_it_matters}
                      </Typography>
                    )}
                    {risk.recommendation && (
                      <Typography
                        variant="body2"
                        sx={{ mt: 0.5, color: "secondary.main", fontWeight: 500 }}
                      >
                        → {risk.recommendation}
                      </Typography>
                    )}
                  </Box>
                );
              })}
            </Box>
          </>
        )}

        {coverageNote && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="caption" color="text.secondary">
              {coverageNote}
            </Typography>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default ExecutiveSummary;
