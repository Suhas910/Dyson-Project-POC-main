import { useRef, useState, useEffect } from "react";
import {
  AppBar,
  Toolbar,
  Typography,
  Button,
  Box,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Divider,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DescriptionIcon from "@mui/icons-material/Description";

import { HEADER_GRADIENT } from "../theme";

const MAX_FILE_SIZE = 100 * 1024 * 1024;

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Header({ onFileUpload, loading, uploadedFile, onClearFile }) {
  const fileInputRef = useRef(null);
  const [processFamilies, setProcessFamilies] = useState([]);
  const [selectedProcessFamily, setSelectedProcessFamily] = useState("");
  const [autoOption, setAutoOption] = useState(null);
  const [materials, setMaterials] = useState([]);
  const [selectedMaterial, setSelectedMaterial] = useState("");

  useEffect(() => {
    const fetchProcessFamilies = async () => {
      try {
        // The vite.config.js proxy will handle this request
        const response = await fetch("/api/process_families");
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setProcessFamilies(data.process_families);
        setAutoOption(data.auto || null);
        // Detection is the default. Choosing the wrong family by hand is the
        // one mistake here that produces a clean-looking report with the
        // relevant rules silently skipped, so the safe option leads.
        if (data.auto) {
          setSelectedProcessFamily(data.auto.key);
        } else if (data.process_families.length > 0) {
          setSelectedProcessFamily(data.process_families[0]);
        }
      } catch (error) {
        console.error("Failed to fetch process families:", error);
        // Optionally, show an error to the user
      }
    };
    fetchProcessFamilies();
  }, []);

  // Material limits differ by grade -- ABS and PP have different minimum wall
  // thicknesses -- so the available materials depend on the process family.
  // Families with no material-specific rules return none and the selector hides.
  useEffect(() => {
    // Under detection the family is not known until the part has been measured,
    // so there is no material list to offer yet.
    if (!selectedProcessFamily || selectedProcessFamily === autoOption?.key) {
      setMaterials([]);
      setSelectedMaterial("");
      return;
    }

    const fetchMaterials = async () => {
      try {
        const response = await fetch(
          `/api/materials?process_family=${encodeURIComponent(
            selectedProcessFamily
          )}`
        );
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setMaterials(data.materials || []);
        setSelectedMaterial("");
      } catch (error) {
        console.error("Failed to fetch materials:", error);
        setMaterials([]);
      }
    };
    fetchMaterials();
  }, [selectedProcessFamily, autoOption]);

  const handleFileChange = (event) => {
    const file = event.target.files[0];
    if (!file) return;

    if (
      !file.name.toLowerCase().endsWith(".step") &&
      !file.name.toLowerCase().endsWith(".stp")
    ) {
      alert("Please upload a .step or .stp file.");
      event.target.value = "";
      return;
    }

    if (file.size > MAX_FILE_SIZE) {
      alert("File is too large. Maximum file size is 100 MB.");
      event.target.value = "";
      return;
    }

    if (!selectedProcessFamily) {
      alert("Please select a process family.");
      event.target.value = "";
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("process_family", selectedProcessFamily);
    if (selectedMaterial) {
      formData.append("material", selectedMaterial);
    }

    onFileUpload(formData);
    event.target.value = "";
  };

  const selectStyles = {
    mr: 2,
    minWidth: 180,
    "& .MuiOutlinedInput-root": {
      color: "white",
      "& .MuiOutlinedInput-notchedOutline": {
        borderColor: "rgba(255, 255, 255, 0.4)",
      },
      "&:hover .MuiOutlinedInput-notchedOutline": {
        borderColor: "rgba(255, 255, 255, 0.8)",
      },
      "&.Mui-focused .MuiOutlinedInput-notchedOutline": { borderColor: "white" },
      "& .MuiSvgIcon-root": { color: "white" },
    },
    "& .MuiInputLabel-root": { color: "rgba(255, 255, 255, 0.8)" },
  };

  return (
    <AppBar
      position="static"
      elevation={0}
      sx={{
        // Navy through to violet: structure on the left where the title sits,
        // brand on the right where the actions are.
        backgroundImage: HEADER_GRADIENT,
        backgroundColor: "brand.navy",
      }}
    >
      <Toolbar>
        <Box
          sx={{ display: "flex", alignItems: "center", gap: 1.5, flexGrow: 1 }}
        >
          <DescriptionIcon sx={{ fontSize: 32 }} />
          <Box>
            <Typography
              variant="h6"
              component="div"
              fontWeight={700}
              lineHeight={1.2}
            >
              Dyson DFM Analysis
            </Typography>
            <Typography variant="caption" sx={{ opacity: 0.8 }}>
              Design for Manufacturability Report
            </Typography>
          </Box>
        </Box>

        {uploadedFile && (
          <Chip
            icon={<DescriptionIcon />}
            label={`${uploadedFile.name} (${formatFileSize(uploadedFile.size)})`}
            variant="outlined"
            sx={{
              mr: 2,
              color: "#fff",
              borderColor: "rgba(255,255,255,0.4)",
              "& .MuiChip-icon": { color: "#fff" },
              maxWidth: 300,
            }}
            onDelete={!loading ? onClearFile : undefined}
          />
        )}

        <FormControl
          size="small"
          sx={{ ...selectStyles, minWidth: 220 }}
          disabled={loading || !processFamilies.length}
        >
          <InputLabel id="process-family-select-label">
            Process Family
          </InputLabel>
          <Select
            labelId="process-family-select-label"
            value={selectedProcessFamily}
            label="Process Family"
            onChange={(e) => setSelectedProcessFamily(e.target.value)}
            // The detect option carries a line of explanation in the menu,
            // which is useful while choosing and noise once chosen. The closed
            // control shows the label alone.
            renderValue={(value) =>
              value === autoOption?.key ? autoOption.label : value
            }
          >
            {autoOption && (
              <MenuItem value={autoOption.key}>
                <Box>
                  <Typography variant="body2" fontWeight={700}>
                    {autoOption.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {autoOption.description}
                  </Typography>
                </Box>
              </MenuItem>
            )}
            {autoOption && <Divider />}
            {processFamilies.map((family) => (
              <MenuItem key={family} value={family}>
                {family}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {materials.length > 0 && (
          <FormControl size="small" sx={selectStyles} disabled={loading}>
            <InputLabel id="material-select-label">Material</InputLabel>
            <Select
              labelId="material-select-label"
              value={selectedMaterial}
              label="Material"
              onChange={(e) => setSelectedMaterial(e.target.value)}
            >
              <MenuItem value="">
                <em>Not specified</em>
              </MenuItem>
              {materials.map((material) => (
                <MenuItem key={material.key} value={material.key}>
                  {material.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}

        <Button
          variant="contained"
          component="label"
          startIcon={
            loading ? (
              <CircularProgress size={18} color="inherit" />
            ) : (
              <UploadFileIcon />
            )
          }
          disabled={loading || !selectedProcessFamily}
          sx={{
            // Inverted against the gradient: a white button on violet reads as
            // the primary action far better than another violet button would.
            whiteSpace: "nowrap",
            backgroundColor: "#FFFFFF",
            color: "primary.main",
            "&:hover": { backgroundColor: "brand.violetTint" },
            "&.Mui-disabled": {
              backgroundColor: "rgba(255,255,255,0.35)",
              color: "rgba(255,255,255,0.7)",
            },
          }}
        >
          {loading ? "Analyzing..." : "Upload STEP File"}
          <input
            ref={fileInputRef}
            type="file"
            hidden
            onChange={handleFileChange}
            accept=".step,.stp"
          />
        </Button>
      </Toolbar>
    </AppBar>
  );
}

export default Header;
