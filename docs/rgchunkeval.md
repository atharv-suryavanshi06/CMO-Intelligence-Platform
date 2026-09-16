# RAG Chunk Evaluation

Generated: 2026-08-20

This evaluation runs the same retrieval path surfaced by the frontend **RAG Trace** (`rag/retrieval/retriever_2.py::retrieve`) against the 30 questions in `Questions.md`. The current Gemini embedding quota was exhausted, so this run uses the retriever’s local keyword-only fallback; no document chunks were sent to Gemini for answer generation.

Completeness rule: **COMPLETE** means the expected source document appears in the top three retrieved chunks and at least two question-topic terms are present in the retrieved evidence. **INCOMPLETE** means one of those checks failed. This is a chunk-retrieval completeness check, not a claim that the generated answer is factually correct.

## Summary

- Questions tested: 30
- COMPLETE: 27
- INCOMPLETE: 3
- Retrieval mode: keyword-only fallback (top 3 chunks per question)

## 1. In the ANA Q1 2025 Programmatic Transparency Benchmark, what total optimization opportunity does the TrueCPM Index report, and what global efficiency gain does it represent?

**Status:** COMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `truecpm`, `optimization`, `efficiency`

### Retrieved chunks

1. `c_e797fd48e1db` — **ANAQ12025Programmatic.pdf**, page(s) [3]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings TrueCPM Index: The newly introduced TrueCPM Index reveals an average 37.8 percent total optimization opportunity across participating marketers representing an estimated global efficiency gain op

2. `c_6489a569ec83` — **ANAQ12025Programmatic.pdf**, page(s) [3]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings Recognizing that this is unrealistic, the 2023 ANA study introduced a simulation that ranked TrueCPM values and reallocated ad spending from the lowest-performing tiers to higher-performing ones.

3. `c_1acc523f997a` — **ANAQ12025Programmatic.pdf**, page(s) [3]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings 1 The TrueCPM Index of 37.8 percent represents the total potential for optimization, indicating the gap needed to achieve 100 percent of impressions meeting quality standards. Recognizing that th

## 2. What three quality conditions must an impression meet to qualify as a TrueImpression?

**Status:** INCOMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** none

### Retrieved chunks

1. `c_0724a7ba3f1e` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [4]
   > Sidebar What are the two or three essential capabilities for CMOs to focus on? 2. Talent management. As leaders, we must create the conditions for others to thrive and ensure that our teams are well selected, motivated, rewarded, and developed. That will help us put the best team

2. `c_0c2c4fb85e94` — **ANAQ12025Programmatic.pdf**, page(s) [21]
   > 8.1 TrueKPI Framework Optimizing value by pricing impressions on quality metrics The TrueKPI Framework evaluates ad impressions based on their quality relative to price using three key metrics: • Truelmpressions: The impressions that meet defined cost, quality and safety requirem

3. `c_4ddc579488fa` — **ANAQ12025Programmatic.pdf**, page(s) [21]
   > 8.1 TrueKPI Framework Custom Truelmpression requirements Marketers can define their own Truelmpressions criteria by selecting specific metrics and assigning values to each. Using the TrueCPM Decision Tree (see next page) and Benchmark data as reference values, they can set their 

## 3. According to the Cost Waterfall, how many cents of each dollar entering a DSP effectively reaches the consumer?

**Status:** COMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `cost waterfall`, `cents`, `dollar`

### Retrieved chunks

1. `c_86fa7979779e` — **ANAQ12025Programmatic.pdf**, page(s) [4]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings ANA The Cost Waterfall provides a holistic market view generated from impression-level matched data between DSP and ad verification platforms. Each section is processed and shown sequentially. Af

2. `c_125249adcd30` — **ANAQ12025Programmatic.pdf**, page(s) [4]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings After accounting for transaction costs and media productivity losses, 41.0 cents of every ad dollar entering a DSP (Demand Side Platform) effectively reaches the consumer. This represents a small

3. `c_4475356120bf` — **ANAQ12025Programmatic.pdf**, page(s) [5]
   > Programmatic Transparency Benchmark Cost Waterfall Comments A simple guide to interpreting Cost Waterfall numbers The Cost Waterfall is built using sequential calculations based on average data from advertisers where log-level data (LLD) is matched between a DSP and an Ad Verific

## 4. How did spending on Made for Advertising (MFA) sites change from the 2023 ANA study to Q1 2025?

**Status:** COMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `mfa`, `made for advertising`, `2023`, `2025`

### Retrieved chunks

1. `c_3127f76cf365` — **ANAQ12025Programmatic.pdf**, page(s) [3]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings TrueAdSpend Index: On average, 41 percent of ad spending is allocated to media delivering impressions matching index requirements (non-IVT, measurable for viewability, viewable). This is a signif

2. `c_b4f0f4e54909` — **ANAQ12025Programmatic.pdf**, page(s) [15]
   > 6.4 Made for Advertising MFA requires ongoing curation to reach levels below 3 percent of web ad spending Media Productivity Half of the Benchmark marketers now allocate less than 2.3 percent of their open web ad spending to MFA websites (as defined by deepsee.io), representing a

3. `c_ef4318f9fe33` — **ANAQ12025Programmatic.pdf**, page(s) [3]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings MFA Under Better Control: Since the 2023 ANA study, the ad spending on MFA sites in the cost waterfall has decreased from 15 percent to 0.4 percent . This shift reflects a growing focus on higher

## 5. How did the median number of SSPs used by marketers change between Q4 2024 and Q1 2025?

**Status:** COMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `ssp`, `median`, `q4`, `q1`

### Retrieved chunks

1. `c_473bc0ea61c1` — **ANAQ12025Programmatic.pdf**, page(s) [12]
   > 4.7 Number of SSPs Q1 2025 19 70 Q4 2024 14 80

2. `c_026e2a231472` — **ANAQ12025Programmatic.pdf**, page(s) [12]
   > 4.7 Number of SSPs Median number of SSPs increased from 14 to 19 The median number of SSPs per advertiser increased from 14 in the Q4 2025 dataset to 19 . This indicates an uptick in the overall number of supply partners used by advertisers surveyed.

3. `c_331ecdc7b72d` — **ANAQ12025Programmatic.pdf**, page(s) [12]
   > 4.6 CPM Paid by Marketers Q1 2025 2.2 5.6 12.1 Q4 2024 1.8 5.8 14.7

## 6. What percentage of reviewed ad spending did CTV represent in Q1 2025, and what were the reported median measurability and viewability values?

**Status:** COMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `ctv`, `measurability`, `viewability`

### Retrieved chunks

1. `c_87b2c0b5b9d7` — **ANAQ12025Programmatic.pdf**, page(s) [3]
   > Programmatic Transparency Benchmark Executive Summary Q1 2025 Benchmark Key Findings Improving CTV Measurements: CTV now represents 30 percent of reviewed ad spending compared to 28 percent in Q4 2024. While CTV remains highly fragmented due to the multitude of platforms, access 

2. `c_656c754dd06d` — **ANAQ12025Programmatic.pdf**, page(s) [10]
   > 4.4 Connected TV Improved Measurability and Viewability Measurability Gains: In Q4 2024, only 0-1.15 percent of ad spending was measurable. By Q1 2025, this increased to a range of 0-99 percent, with an average of 22.7 percent and a median of 61.6 percent, marking significant pro

3. `c_2ca5dceb8df5` — **ANAQ12025Programmatic.pdf**, page(s) [10]
   > 4.4 Connected TV Improved Measurability and Viewability Viewability Metrics: Viewability spanned 0-81.9 percent of ad spending, with an average of 33.2 percent and a median of 21.4 percent. Methodology variation across vendors and 5 Source: eMarketer Dec, 2024

## 7. How does the TrueApple analogy explain the difference between CPM and TrueCPM?

**Status:** COMPLETE  
**Expected source:** `ANAQ12025Programmatic.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `trueapple`, `truecpm`, `cpm`

### Retrieved chunks

1. `c_43ae86e67be5` — **ANAQ12025Programmatic.pdf**, page(s) [6]
   > 2.1 TrueCPM Index explained Let's call them apples instead of impressions for this analogy To explain how the TrueCPM Index is calculated, the TrueApple Index is counting apples as a proxy for impressions . Let's assume that there are good apples, the ones that are good to eat, a

2. `c_8f16fc137f69` — **ANAQ12025Programmatic.pdf**, page(s) [21]
   > 8.1 TrueKPI Framework Reducing g the TrueCPM Delta to enhance efficiency As illustrated in the example above, the difference between a $5.00 CPM and a $7.14 TrueCPM (Scenario A) represents a $2.14 TrueCPM Delta, indicating ad spending on non- productive impressions. This delta si

3. `c_885f179d63d6` — **ANAQ12025Programmatic.pdf**, page(s) [6]
   > 2.1 TrueCPM Index explained TrueApple Index True Cost

## 8. How does the McKinsey article define modern marketing beyond simply running digital campaigns?

**Status:** INCOMPLETE  
**Expected source:** `modern-marketing-what-it-is-what-it-isnt.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `modern marketing`

### Retrieved chunks

1. `c_91db25026457` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [8]
   > Where are you on your journey to modern marketing? Pricing is driven by a combination of research and analytics within operational constraints. Organizational design & culture How are the culture and organization model evolving to support the modernization of your marketing capab

2. `c_0ac368cc80b3` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [6]
   > To operate with an ROI mindset, everyone needs to operate as if the money they are spending is their own. 2. Agile marketing at scale: Getting serious about moving beyond pilots 2 David Edelman and Jason Heller, 'How digital marketing operations can transform business,' July 2015

3. `c_d04b2f118d5b` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [8]
   > Where are you on your journey to modern marketing? Our marketing organization and culture have not changed significantly beyond adding new digital capabilities. Our culture has changed significantly to nurture modern marketing talent, and our organization model elevates and empha

## 9. What percentage of global CEOs said they expect marketing to be a major driver of most or all of the company’s growth agenda?

**Status:** COMPLETE  
**Expected source:** `modern-marketing-what-it-is-what-it-isnt.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `ceos`, `growth agenda`, `driver`

### Retrieved chunks

1. `c_3e58759ced1b` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [2]
   > Modern marketing: What it is, what it isn't, and how to do it While these examples contain some of the hallmarks of modern marketing, in our view it is much bigger than that. Modern marketing is the ability to harness the full capabilities of the business to provide the best expe

2. `c_8ac9a09087cc` — **UPDATED_TwG_CMO_CFO_Media_Effectiveness_Guide.pdf**, page(s) [2]
   > | research | | shows that even though 83% of CEOs see marketing as a growth driver, | | 45% of | | --- | --- | --- | --- | --- | | CFOs have declined a marketing budget | | | because it didn't demonstrate a clear line to | | | value. | Clearly, there is work to be done to link ma

3. `c_bf6bdd5f12b2` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [62]
   > Marketing expenses are a major focus of cost cutting before other areas, across all sectors and industries S↓

## 10. Which capabilities and mindsets does the modern-marketing article say companies need to modernize to drive digital-age growth?

**Status:** COMPLETE  
**Expected source:** `modern-marketing-what-it-is-what-it-isnt.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `capabilities`, `mindsets`, `modernize`

### Retrieved chunks

1. `c_f8ee9ca7264a` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [1]
   > Modern marketing: What it is, what it isn't, and how to do it To drive growth in the digital age, marketing needs to modernize a specific set of capabilities and mindsets. by Sarah Armstrong, Dianne Esber, Jason Heller, and Björn Timelin What does 'modern marketing' mean to you? 

2. `c_5a6304274f21` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [5]
   > Sidebar Enablers: Operating like a modern marketer To modernize marketing's capabilities, marketing organizations need to upgrade four key underlying operational enablers.

3. `c_2c6901170841` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson To drive revenue growth in the digital age, new data shows that marketing leaders are upgrading data-collection technology, collaborating closely with IT, and focusing on test-and-learn agility. Given 

## 11. Why does the article argue that marketing departments need a new way of operating to deliver customer growth?

**Status:** COMPLETE  
**Expected source:** `modern-marketing-what-it-is-what-it-isnt.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `operating model`, `marketing department`

### Retrieved chunks

1. `c_2fbadbc2cab0` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [2]
   > Modern marketing: What it is, what it isn't, and how to do it Delivering on this promise requires a whole new way of operating. Marketing departments need to be rewired for speed, collaboration, and customer focus. It's less about changing what marketing

2. `c_c1675b9bef74` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) The new Marketing Operating Model (MOM) Distribution platforms: Marketing-technology platforms are the last mile of the process. They integrate the customer scores and use them as triggers to deliver the right message to the right per

3. `c_6ac63f92d2df` — **AI-marketing-playbook 4.pdf**, page(s) [33]
   > Figure 6. Focus on responsible use Why prioritization matters As AI experimentation gives way to scaled deployment, leading marketing organizations are moving beyond 'shiny object' pilots. The focus must now shift to AI applications that solve real business problems, can be repli

## 12. What 2025 U.S. ad-spend growth rate does the IAB Outlook Study project, and how does it compare with 2024’s event-driven growth?

**Status:** COMPLETE  
**Expected source:** `IAB_Outlook_-Study_January_16_2025_v2.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `2025`, `growth`, `outlook`

### Retrieved chunks

1. `c_0f8ddd14b060` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [1]
   > 2025 Outlook Study A Snapshot into U.S. Ad Spend, Opportunities, and Strategies for Growth January 2025

2. `c_3b558089bf93` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [1]
   > | 2025 Outlook Study A Snapshot into U.S. Ad Spend, Opportunities, and Strategies for Growth January 2025 | | | --- | --- | | | |

3. `c_d276a49e3760` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [6]
   > Ad spend will continue to grow in 2025, but unlikely match 2024's cyclical event -driven surge Following a double-digit ad spend increase in 2024 driven by the Presidential Election and the Summer Olympics, buyers anticipate a more modest growth rate of 7.3% in 2025. PROJECTED % 

## 13. Which channels are expected to post double-digit growth in the IAB 2025 Outlook Study, and what happens to linear TV?

**Status:** COMPLETE  
**Expected source:** `IAB_Outlook_-Study_January_16_2025_v2.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `double-digit`, `linear tv`, `channels`

### Retrieved chunks

1. `c_5f24a7bca34e` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [7]
   > The fastest-growing channels continue to be where consumers and commerce thrive, and sight-sound-motion converge CTV, social media, and retail media (see page 9) are expected to post double-digit growth in 2025, offering buyers the most advanced personalization and measurement ca

2. `c_1adf85823a1e` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [4]
   > Key Insights 01 Ad spend will continue to grow in 2025 (+7.3% YoY) but will be slower than 2024's cyclical event -driven surge (+11.8%). 02 Led by double-digit growth in CTV, social media, and retail media, all digital channels will post growth this year. Linear TV will dip sharp

3. `c_ab5d441d65c3` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [4]
   > Key Insights 02 Led by double-digit growth in CTV, social media, and retail media, all digital channels will post growth this year. Linear TV will dip sharply YoY due to the absence of cyclical events. 03 Amid product inflation, ad ecosystem fragmentation, and measurement challen

## 14. What priorities are buyers adopting in response to inflation, ecosystem fragmentation, and measurement challenges?

**Status:** COMPLETE  
**Expected source:** `IAB_Outlook_-Study_January_16_2025_v2.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `inflation`, `fragmentation`, `measurement`

### Retrieved chunks

1. `c_6c6a5e1cbdc8` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [4]
   > Key Insights 03 Amid product inflation, ad ecosystem fragmentation, and measurement challenges, buyers are prioritizing customer acquisition and crossplatform solutions along with optimizing R/F and MMM tactics. 04 Despite the nascency of certain applications, 80% of buyers are u

2. `c_ab5d441d65c3` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [4]
   > Key Insights 02 Led by double-digit growth in CTV, social media, and retail media, all digital channels will post growth this year. Linear TV will dip sharply YoY due to the absence of cyclical events. 03 Amid product inflation, ad ecosystem fragmentation, and measurement challen

3. `c_cca97689da2b` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [11]
   > Buyers are prioritizing customer acquisition and optimizing R/F and MMM amid product inflation and ad ecosystem fragmentation An increased focus on customer acquisition suggests that brands are looking to generate more revenue to compensate, among other things, for smaller margin

## 15. Which topics does the IAB Outlook Study cover besides ad-spend projections, including measurement, generative AI, and performance investment?

**Status:** COMPLETE  
**Expected source:** `IAB_Outlook_-Study_January_16_2025_v2.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `measurement`, `generative ai`, `performance`

### Retrieved chunks

1. `c_af321d49a383` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [3]
   > Foreword The report provides 2025 ad spend projections for the market overall and at the channel level, while addressing critical topics including cross-platform measurement, generative AI adoption, and performance-focused investment. It surfaces key shifts in priorities and stra

2. `c_4b1968d2c1fd` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [5]
   > 2025 Ad Spend Outlook 01 iab.

3. `c_0f8ddd14b060` — **IAB_Outlook_-Study_January_16_2025_v2.pdf**, page(s) [1]
   > 2025 Outlook Study A Snapshot into U.S. Ad Spend, Opportunities, and Strategies for Growth January 2025

## 16. Why does the privacy playbook recommend strengthening responsibly gathered first-party data and clearly communicating data practices?

**Status:** COMPLETE  
**Expected source:** `2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `first-party`, `responsibly`, `data practices`

### Retrieved chunks

1. `c_d258fc4f0a58` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [14]
   > Unlock more accurate conversion measurement with first-party data Once you've established a first-party data foundation from practices like sitewide tagging, you can unlock more comprehensive reporting with other privacy-safe solutions. For example, enhanced conversions for web a

2. `c_ac6802426139` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [2]
   > Executive s summary People are concerned about how their personal information is collected and used online. Strengthen your foundation of responsibly gathered first-party data through clear communication about your data practices. marketing analytics Google Analytics for Firebase

3. `c_2e5591defa20` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [4]
   > The marketer's role As marketers, you're in a unique position to help your organization prepare for the future of advertising and to get the most out of your marketing investments. Forming a strategy powered by responsibly gathered first-party data and Google's AI-powered solutio

## 17. How can first-party data provide a more accurate view of how users convert as technology platforms evolve?

**Status:** COMPLETE  
**Expected source:** `2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `first-party`, `convert`, `measurement`

### Retrieved chunks

1. `c_d258fc4f0a58` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [14]
   > Unlock more accurate conversion measurement with first-party data Once you've established a first-party data foundation from practices like sitewide tagging, you can unlock more comprehensive reporting with other privacy-safe solutions. For example, enhanced conversions for web a

2. `c_4c9dcfbee5f1` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [2]
   > Tech platforms are evolving Enable your first-party data to Google tag and Google Tag techniques – such as device give a more accurate view of Manager IDs and third-party cookies – how users convert. that advertisers have relied Enhanced conversions for web on for decades to reac

3. `c_afd38c54c29a` — **AI-marketing-playbook 4.pdf**, page(s) [20]
   > The data challenges Foster collaboration As AI adoption accelerates, connecting and governing data across platforms will determine how quickly marketing teams can act on reliable insights. 'If you can't connect the different data points together to consumers so that you have a co

## 18. Which Google measurement and activation techniques are presented as ways to use first-party data across online touchpoints?

**Status:** COMPLETE  
**Expected source:** `2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `google`, `measurement`

### Retrieved chunks

1. `c_4c9dcfbee5f1` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [2]
   > Tech platforms are evolving Enable your first-party data to Google tag and Google Tag techniques – such as device give a more accurate view of Manager IDs and third-party cookies – how users convert. that advertisers have relied Enhanced conversions for web on for decades to reac

2. `c_988a2c4cf2ab` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [17]
   > Case study eCampus University measured +12% more conversions with privacy-centric measurement solutions Use machine learning to make sense of available signals and get accurate measurement As you build a strong foundation of first-party data across multiple products, you'll need 

3. `c_2648faa3a81b` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [16]
   > Case study eCampus University measured +12% more conversions with privacy-centric measurement solutions Approach: Amid new regulations and browser restrictions, eCampus University, an online university in Italy, needed a way to properly measure conversions. It worked with its Goo

## 19. How do first-party-data insights support relevant customer experiences, targeting, and return on investment?

**Status:** INCOMPLETE  
**Expected source:** `2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `targeting`

### Retrieved chunks

1. `c_78ba67caa01b` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [2]
   > Executive s summary People turn to brands that can anticipate their needs and deliver helpful and relevant experiences. Multiply your customer connections at scale using insights from your first-party data. expansion Optimized targeting in Google Ads a and Display & Video 360 Pub

2. `c_518bef2cb146` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [9]
   > Generate and connect first-party data from your customer relationships Once you have identified how to create meaningful , memorable , and manageable experiences for your customers, it is important to generate a comprehensive and actionable first-party database that aligns with y

3. `c_28bea30fbdf0` — **2023_Privacy_Playbook_for_privacy_and_performance_APAC.pdf**, page(s) [2]
   > Executive s summary How businesses can respond Build more meaningful customer relationships. Earn people's trust to unlock more first-party data.

## 20. In the Global CMO Growth Council AI Playbook, why is data described as both an accelerator and a brake for AI in marketing?

**Status:** COMPLETE  
**Expected source:** `AI-marketing-playbook 4.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `accelerator`, `brake`, `data`

### Retrieved chunks

1. `c_a7900aef8f9a` — **AI-marketing-playbook 4.pdf**, page(s) [2]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK and the brake AI built on a solid foundation The data challenges From AI pilots to embedded marketing workflows The most effective approach Responsible AI for growth The human element of AI The AI literacy foundation The CMO's untapped AI val

2. `c_8f66df08a635` — **AI-marketing-playbook 4.pdf**, page(s) [2]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK Global CMO Growth Council AI Playbook | 3 External Document © 2026 Infosys Limited Knowledge Institute Contents Introduction Executive summary Short- and long-term implications of AI in marketing Enterprise AI: What CMOs need to know Data is 

3. `c_d8ea4e52241a` — **AI-marketing-playbook 4.pdf**, page(s) [1]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK Agents InfOSYS |Knowledge Institute

## 21. What challenges must organizations address when moving from AI pilots to embedded marketing workflows?

**Status:** COMPLETE  
**Expected source:** `AI-marketing-playbook 4.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `pilots`, `workflow`, `embedded`

### Retrieved chunks

1. `c_6ac63f92d2df` — **AI-marketing-playbook 4.pdf**, page(s) [33]
   > Figure 6. Focus on responsible use Why prioritization matters As AI experimentation gives way to scaled deployment, leading marketing organizations are moving beyond 'shiny object' pilots. The focus must now shift to AI applications that solve real business problems, can be repli

2. `c_a7900aef8f9a` — **AI-marketing-playbook 4.pdf**, page(s) [2]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK and the brake AI built on a solid foundation The data challenges From AI pilots to embedded marketing workflows The most effective approach Responsible AI for growth The human element of AI The AI literacy foundation The CMO's untapped AI val

3. `c_4cc6a0f159fd` — **AI-marketing-playbook 4.pdf**, page(s) [26]
   > Takeaways Start small but design for scale: MVPs are essential but always plan for how successful pilots will be embedded and scaled. Operationalize, don't isolate: AI must become part of the marketing muscle memory embedded in workflows, not just run as experiments. Measure and 

## 22. What does the AI Playbook mean by responsible AI for growth?

**Status:** COMPLETE  
**Expected source:** `AI-marketing-playbook 4.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `responsible ai`, `growth`

### Retrieved chunks

1. `c_d8ea4e52241a` — **AI-marketing-playbook 4.pdf**, page(s) [1]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK Agents InfOSYS |Knowledge Institute

2. `c_a7900aef8f9a` — **AI-marketing-playbook 4.pdf**, page(s) [2]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK and the brake AI built on a solid foundation The data challenges From AI pilots to embedded marketing workflows The most effective approach Responsible AI for growth The human element of AI The AI literacy foundation The CMO's untapped AI val

3. `c_7cc418539d78` — **AI-marketing-playbook 4.pdf**, page(s) [5]
   > Executive summary AI 上 6 | Global CMO Growth Council AI Playbook External Document © 2026 Infosys Limited Knowledge Institute

## 23. Why does the AI Playbook emphasize the human element of AI adoption in marketing?

**Status:** COMPLETE  
**Expected source:** `AI-marketing-playbook 4.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `human`, `ai`

### Retrieved chunks

1. `c_a7900aef8f9a` — **AI-marketing-playbook 4.pdf**, page(s) [2]
   > GLOBAL CMO GROWTH COUNCIL AI PLAYBOOK and the brake AI built on a solid foundation The data challenges From AI pilots to embedded marketing workflows The most effective approach Responsible AI for growth The human element of AI The AI literacy foundation The CMO's untapped AI val

2. `c_3b38a1286a33` — **AI-marketing-playbook 4.pdf**, page(s) [30]
   > The human element of AI 'For us, it's not when to include the human because they're always there ... it's just to what extent and how.' Chief growth officer Consumer goods company

3. `c_91fc352a40c7` — **AI-marketing-playbook 4.pdf**, page(s) [8]
   > Short-term implications Measurement Faster insights: AI accelerates campaign analysis, enabling near-realtime optimization. Return on investment (ROI) focus: Early AI deployments emphasize cost savings and productivity. Human in the loop: Marketing teams must oversee outputs to p

## 24. What does MOM stand for in “Meet your new MOM,” and what business problem is the Marketing Operating Model intended to address?

**Status:** COMPLETE  
**Expected source:** `Meet-your-new-MOM.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `mom`, `marketing operating model`

### Retrieved chunks

1. `c_12f37b1bd3f5` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) The new Marketing Operating Model (MOM) A set of business rules and regression models, increasingly based on machine learning, helps to prioritize and match specific messages, offers, and experiences to specific customer scores.

2. `c_bc74f3f3e4b3` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) How to build up your MOM The best-performing companies are investing in marketing technology (martech) thoughtfully, working more closely with IT in a more agile way. At the same time, some clear shortfalls are exposing fault lines th

3. `c_fa11b4f8d764` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson The survey was conducted online in October 2016. The 217 respondents were self-qualified in roles of marketing director, VP and CMO / marketing leadership. Respondents were drawn from the ANA Survey Co

## 25. How are marketing leaders upgrading data-collection technology and collaborating with IT according to the MOM article?

**Status:** COMPLETE  
**Expected source:** `Meet-your-new-MOM.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `data`, `technology`, `it`

### Retrieved chunks

1. `c_2c6901170841` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson To drive revenue growth in the digital age, new data shows that marketing leaders are upgrading data-collection technology, collaborating closely with IT, and focusing on test-and-learn agility. Given 

2. `c_97dec1309edd` — **modern-marketing-what-it-is-what-it-isnt.pdf**, page(s) [9]
   > 4. Data and technology: An obsession for looking ahead Instead of the traditional approach, where IT takes the lead in data management, marketing leaders should work with IT leaders to develop a shared vision for how data will be accessed and used. This starts with the CMO and CT

3. `c_6c06be3e94aa` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson To drive revenue growth and improve customer experience, technology has to enable more efficient data collection, foster cross-functional collaboration, and support test-and-learn agility. It needs, in

## 26. Why is test-and-learn agility important in the Marketing Operating Model?

**Status:** COMPLETE  
**Expected source:** `Meet-your-new-MOM.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `test-and-learn`, `agility`

### Retrieved chunks

1. `c_6c06be3e94aa` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson To drive revenue growth and improve customer experience, technology has to enable more efficient data collection, foster cross-functional collaboration, and support test-and-learn agility. It needs, in

2. `c_2c6901170841` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson To drive revenue growth in the digital age, new data shows that marketing leaders are upgrading data-collection technology, collaborating closely with IT, and focusing on test-and-learn agility. Given 

3. `c_25cc802c5944` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson Technology is a crucial element in enabling marketers to meet those expectations. But technology is only a partial answer, as any marketer whose tech spend has produced less than optimal results can te

## 27. How did consumer expectations for near-real-time interactions change in the ANA disruption survey cited by the MOM article?

**Status:** COMPLETE  
**Expected source:** `Meet-your-new-MOM.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `near-real-time`, `interactions`, `expectations`

### Retrieved chunks

1. `c_58f9ca57f0ec` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson Their message to businesses is paraphrased by Veruca Salt in Charlie and the Chocolate Factory : 'I want it, and I want it now. In fact, consumer expectations of near-real-time interactions and service

2. `c_4823e7322858` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson In fact, consumer expectations of near-real-time interactions and services jumped to number one on the Association of National Advertisers (ANA) 2016 survey of business disruptions, 1 up from sixth pla

3. `c_bc74f3f3e4b3` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) How to build up your MOM The best-performing companies are investing in marketing technology (martech) thoughtfully, working more closely with IT in a more agile way. At the same time, some clear shortfalls are exposing fault lines th

## 28. How do one-click shopping, same-day delivery, instant search, and ubiquitous media affect the operating model marketers need?

**Status:** COMPLETE  
**Expected source:** `Meet-your-new-MOM.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `one-click`, `same-day`, `instant search`

### Retrieved chunks

1. `c_d32d5b35b5b3` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson Given their access to one-click shopping, same-day delivery, instant search results, and ubiquitous media, consumer expectations are sky high. Their message to businesses is paraphrased by Veruca Salt 

2. `c_2c6901170841` — **Meet-your-new-MOM.pdf**, page(s) [1]
   > Meet your new MOM (Marketing Operating Model) Jason Heller and Kelsey Robinson To drive revenue growth in the digital age, new data shows that marketing leaders are upgrading data-collection technology, collaborating closely with IT, and focusing on test-and-learn agility. Given 

3. `c_bc74f3f3e4b3` — **Meet-your-new-MOM.pdf**, page(s) [2]
   > Meet your new MOM (Marketing Operating Model) How to build up your MOM The best-performing companies are investing in marketing technology (martech) thoughtfully, working more closely with IT in a more agile way. At the same time, some clear shortfalls are exposing fault lines th

## 29. What kinds of economic pressure on marketing contracts are discussed in the 2026 CMO Survey Highlights and Insights Report?

**Status:** COMPLETE  
**Expected source:** `The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `economic`, `contracts`, `pressure`

### Retrieved chunks

1. `c_2c471055a85b` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [1]
   > The e cmo Survey® Highlights and Insights Report t2026

2. `c_bb480b3a08d4` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [1]
   > Marketing Contracts Under Economic Pressure Desi g i Gig oins Deloitte.

3. `c_11af798daf0b` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [1]
   > Marketing Contracts Under Economic Pressure Desi g i Gig oins DUKE FUQUA

## 30. What actions or strategic responses does the 2026 CMO Survey report recommend for marketing leaders facing changing growth, customer, and economic conditions?

**Status:** COMPLETE  
**Expected source:** `The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf`  
**Expected source retrieved:** yes  
**Topic-term hits:** `strategic`, `growth`

### Retrieved chunks

1. `c_d45896977274` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [43]
   > cmo Survey The Marketing responsibilities have grown across most activities since 2025 Top Economic Sector: Top Industry Sector: Top % Online Sales: 2026

2. `c_f155abd9005d` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [40]
   > cmo Survey The On average, marketing leaders report their organizations revise priorities in the face of change but fewer build capabilities to support it Build capabilities that facilitate agile marketing actions Sense emerging opportunities and threats in the marketplace Sense 

3. `c_7241c6357e2a` — **The_CMO_Survey-Highlights_and_Insights_Report-2026.pdf**, page(s) [29]
   > The underlying strategic rationale for growth spending allocations The cMo Survey What is the underlying strategic rationale driving this spending allocation? Responses reflect the companies that over-index (20% more than the average) on the use of the specific strategy. The unde


