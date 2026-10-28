<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">

<xsl:template match="/">
<html>
	<head>
		<link href="/style.css" rel="stylesheet" type="text/css"/>
		<meta name="copyright" content="Copyright (C) 財務省 Ministry of Finance, The Japanese Government"/>
		<meta name="robots" content="NOINDEX,NOFOLLOW"/>
		<meta  http-equiv="Content-Style-Type" content="text/css"/>
		<title>財務省貿易統計　Trade Statistics of Japan</title>
	</head>
	<body>
		<a name="top"></a>
		<a name="pg1"></a>
		<form name="form1" method="POST"/>
		<noscript>ブラウザのJavascript機能をONにしないと、貿易統計検索が行なえません。</noscript>
		<div id="base">
		<!--header start-->
		<a name="top"/>
		<div id="header">
		<div class="logo" style="height:10px;">

		</div>
		<div class="mainvisual" style="background-image:url(/toukei/common2018/img/head-bg.jpg);background-repeat:repeat-x;width:100%;clear:both;"><img src="/toukei/english/common2018/img/head-logo.jpg" alt="Japanese Customs IMG" width="360" height="43"></img></div>
		</div>
		<!--header end-->
		<!--topmenu start-->
		<div id="topmenu" style="height:40px;"><ul class="inline">
		<a href="/toukei/info/index_e.htm"><img src="/toukei/english/common2018/img/gnavi-toppage.gif" alt="Top Page" width="160" height="40"/></a>
		<a href="/toukei/srch/indexe.htm"><img src="/toukei/english/common2018/img/gnavi-search.gif" alt="Search"  width="164" height="40"/></a>
		<a href="/toukei/info/tsdl_e.htm"><img src="/toukei/english/common2018/img/gnavi-download.gif" alt="Data Download"  width="164" height="40"/></a>
		<a href="/toukei/shinbun/happyou_e.htm"><img src="/toukei/english/common2018/img/gnavi-pr.gif" alt="Press Release"  width="164" height="40"/></a>
		<a href="/toukei/info/contac_e.htm"><img src="/toukei/english/common2018/img/gnavi-contact.gif" alt="Contact"  width="164" height="40"/></a>
		<a href="/english/index.htm"><img src="/toukei/english/common2018/img/gnavi-customs.gif" alt="Japan Customs Website"  width="164" height="40"/></a>
		</ul></div>
		<!--topmenu end-->
		<!--maincolumn start-->
		<div id="maincolumn">
		<a name="pg01"/>
		<table class="normal">
		<tr>
			<td>
				<div align="left">
				<xsl:for-each select="hodoxml/pdf">
					<img src="/toukei/img/common/arrow_att.gif" alt="PDF for Print" width="12"/><a><xsl:attribute name="href"><xsl:value-of select="@href"/></xsl:attribute>PDF for Print</a>
				</xsl:for-each>
				</div>
			</td>
			<td>
				<div align="right"><span>
				<xsl:value-of select="hodoxml/sogakutsuki//kohyoymd"/> Release<br/>
				Ministry of Finance
				</span></div>
			</td>
		</tr>
		</table>
		<h2 class='contents'>
		<div align="center">
		<u>Trade Statistics</u>
		</div>
		</h2>
		<!--contents start-->
		<div id="contents">
		<div class="srch">
		<div align="center">
		<xsl:value-of select="hodoxml/sogakutsuki/title"/>
		</div>
		<table class="normal">
			<tr>
				<td><div align="left">Total ( Census, not seasonally adjusted )</div></td>
				<td><p align="right"><SPAN>(Unit: millions of YEN, % )</SPAN></p></td>
			</tr>
		</table>
		<table border="1" class="standard">
		<xsl:for-each select="hodoxml/sogakutsuki">
			<tr>
				<th width="15%" align="center"> </th>
				<th width="35%" align="right"><xsl:value-of select="taishoymtonen"/></th>
				<th width="35%" align="right"><xsl:value-of select="taishoymzennen"/></th>
				<th width="15%" align="right">Percent Change</th>
			</tr>
		</xsl:for-each>
		<xsl:for-each select="hodoxml/sogakutsuki/export">
			<tr>
				<td class="bdr" align="center">Exports</td>
				<td align="right"><xsl:value-of select="sogakutonen"/></td>
				<td align="right"><xsl:value-of select="sogakuzennen"/></td>
				<td align="right"><xsl:value-of select="nobiritsu"/></td>
			</tr>
		</xsl:for-each>
		<xsl:for-each select="hodoxml/sogakutsuki/import">
			<tr>
				<td class="bdr" align="center">Imports</td>
				<td align="right"><xsl:value-of select="sogakutonen"/></td>
				<td align="right"><xsl:value-of select="sogakuzennen"/></td>
				<td align="right"><xsl:value-of select="nobiritsu"/></td>
			</tr>
		</xsl:for-each>
		<xsl:for-each select="hodoxml/sogakutsuki/sashihiki">
			<tr>
				<td class="bdr" align="center">Balance</td>
				<td align="right"><xsl:value-of select="sogakutonen"/></td>
				<td align="right"><xsl:value-of select="sogakuzennen"/></td>
				<td align="right"><xsl:value-of select="nobiritsu"/></td>
			</tr>
		</xsl:for-each>
		</table>
		<br/>
		(Notes )
		<ol>
			<xsl:if test="hodoxml/sogakutsuki/chushaku='1'">
				<li>The value of exports is based upon the F.O.B. value, while the value of imports is based upon the C.I.F. value. (The value of goods, which was imported by Authorized Importers or Authorized Customs Brokers, but not declared for duty payment yet, is based upon the value submitted at the declaration for release of goods.) </li>
			</xsl:if>
			<xsl:if test="hodoxml/sogakutsuki/chushaku='2'">
				<li>The value of exports is based upon the F.O.B. value, while the value of imports is based upon the C.I.F. value. </li>
			</xsl:if>
				<li>Statistical timing - Exports : Entry is made in the month within which falls the date when a ship or aircraft loading the goods concerned leaves a Japanese port. - Imports : Entry is made in the month within which falls the date of the import permit. </li>
				<li>"Percent Change" means "Percent Change from the same month in the preceding year." </li>
		</ol>
		<hr/>
		<p>
			Questions should be referred to :<br/>
			Trade Statistics Section<br/>
			Tariff Policy and Legal Division<br/>
			Customs and Tariff Bureau<br/>
			Ministry of Finance<br/>
			Phone +81-3-3581-4111 Ex. 2518, 2515, 2514<br/>
			(Sorry, we don't provide Statistical data over the telephone to avoid 
			miscommunication. )
		</p>
		<hr/>
		<br/>
		</div>
		<br/>
		Contents
		<ol>
			<xsl:for-each select="hodoxml/mokuji/page">
				<li><a><xsl:attribute name="href"><xsl:value-of select="@href"/></xsl:attribute><xsl:value-of select="pagemei"/></a></li>
			</xsl:for-each>
		</ol>
		<br/>
		<div align="center"><xsl:value-of select="hodoxml/sogakutsuki/page"/></div>
		<br/>
		<!--contents end-->
		</div>
		<br style="page-break-after:always;"/>
		<!--contents start-->
		<div id="contents">
			<xsl:apply-templates select="hodoxml/chiikikunisogaku"/>
		<!--contents end-->
		</div>
		<br style="page-break-after:always;"/>
		<!--contents start-->
		<div id="contents">
			<xsl:apply-templates select="hodoxml/shuyochiikikunihin"/>
		<!--contents end-->
		</div>
		<br style="page-break-after:always;"/>
		<!--contents start-->
		<div id="contents">
			<xsl:apply-templates select="hodoxml/shisu"/>
		<!--contents end-->
		</div>
		<br style="page-break-after:always;"/>
		<!--contents start-->
		<div id="contents">
			<xsl:apply-templates select="hodoxml/chiikishisu"/>
		<!--contents end-->
		</div>
		<br style="page-break-after:always;"/>
	<!--maincolumn end-->
	</div>
	</div>
	</body>
<a name="footermenu"></a>
<div id="footer">
Copyright(C) Ministry of Finance Japan
</div>
</html>
</xsl:template>

<xsl:template match="chiikikunisogaku">
		<a><xsl:attribute name="name"><xsl:value-of select="@name"/></xsl:attribute></a>
		<div align="right">
		<div class="pagetop">
		<img src="/toukei/img/common/arrow_up.gif" alt="Top of Page" width="9"/><a href="#top">
		Top of Page
		</a></div></div>
		<hr/>
		<br style="page-break-after:always;"/>
		<div align="right"><xsl:value-of select="taishokikan"/></div><br/>
		<h2 class='contents'>
		Value of Exports and Imports by Area (Country)
		</h2>

		<div class="srch">
		<div align="right">(Unit: millions of YEN,%)</div>
		<table class="standard">
			<tr>
				<th width="25%" align="center" ></th>
				<th width="15%" align="center">Exports</th>
				<th width="10%" align="center">Percent Change</th>
				<th width="15%" align="center">Imports</th>
				<th width="10%" align="center">Percent Change</th>
				<th width="15%" align="center">Balance</th>
				<th width="10%" align="center">Percent Change</th>
			</tr>
			<xsl:for-each select="chiikikunisogakuinfo">
				<xsl:if test="chiikikunikbn='0'">
					<tr>
						<td class="bdr" align="center"><u><xsl:value-of select="chiikikuni"/></u></td>
						<td class="bdr" align="right"><xsl:value-of select="exportkagakue"/></td>
						<td class="bdr" align="right"><xsl:value-of select="exportnobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="importkagakue"/></td>
						<td class="bdr" align="right"><xsl:value-of select="importnobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="sashihikikagakue"/></td>
						<td class="bdr" align="right"><xsl:value-of select="sashihikinobiritsu"/></td>
					</tr>
				</xsl:if>
				<xsl:if test="chiikikunikbn='1'">
					<tr>
						<td class="bdr" ><u><xsl:value-of select="chiikikuni"/></u></td>
						<td class="bdr" align="right"><xsl:value-of select="exportkagakue"/></td>
						<td class="bdr" align="right"><xsl:value-of select="exportnobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="importkagakue"/></td>
						<td class="bdr" align="right"><xsl:value-of select="importnobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="sashihikikagakue"/></td>
						<td class="bdr" align="right"><xsl:value-of select="sashihikinobiritsu"/></td>
					</tr>
				</xsl:if>
				<xsl:if test="chiikikunikbn='2'">
					<tr>
						<td>　<xsl:value-of select="chiikikuni"/></td>
						<td align="right"><xsl:value-of select="exportkagakue"/></td>
						<td align="right"><xsl:value-of select="exportnobiritsu"/></td>
						<td align="right"><xsl:value-of select="importkagakue"/></td>
						<td align="right"><xsl:value-of select="importnobiritsu"/></td>
						<td align="right"><xsl:value-of select="sashihikikagakue"/></td>
						<td align="right"><xsl:value-of select="sashihikinobiritsu"/></td>
					</tr>
				</xsl:if>
			</xsl:for-each>
		</table>
		（Notes）・ "Percent Change" means "Percent Change from the same term in the preceding year." Figures with "*" means "Multiplier to the same term in the preceding year."<br/>
		<br/>
		<div align="center"><xsl:value-of select="page"/></div>
		<br/>
		</div>
</xsl:template>
<xsl:template match="shuyochiikikunihin">
		<a><xsl:attribute name="name"><xsl:value-of select="@name"/></xsl:attribute></a>
		<div align="right">
		<div class="pagetop">
		<img src="/toukei/img/common/arrow_up.gif" alt="Top of Page" width="9"/><a href="#top">
		Top of Page
		</a></div></div>
		<hr/>
		<br style="page-break-after:always;"/>
		<div align="right"><xsl:value-of select="taishokikan"/></div><br/>
		<h2 class='contents'>
		<xsl:value-of select="title"/>
		</h2>

		<div class="srch">
		<div align="right">(Unit: millions of YEN,%)</div>
		<table class="standard">
			<tr>
				<th width="25%" align="center" ></th>
				<th width="9%" align="center">Unit</th>
				<th width="15%" align="center">Quantity</th>
				<th width="9%" align="center">Percent Change</th>
				<th width="15%" align="center">Value</th>
				<th width="9%" align="center">Share</th>
				<th width="9%" align="center">Percent Change</th>
				<th width="9%" align="center">Contribution degree</th>
			</tr>
			<xsl:for-each select="shuyochiikikunihininfo">
				<xsl:if test="shuyoshohinbunrui='0'">
					<tr>
						<td class="bdr" align="center"><u><xsl:value-of select="shuyoshohin"/></u></td>
						<td class="bdr" align="left"><xsl:value-of select="tani"/></td>
						<td class="bdr" align="right"><xsl:value-of select="suryo"/></td>
						<td class="bdr" align="right"><xsl:value-of select="suryonobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="kagaku"/></td>
						<td class="bdr" align="right"><xsl:value-of select="koseihi"/></td>
						<td class="bdr" align="right"><xsl:value-of select="kagakunobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="zogenkiyodo"/></td>
					</tr>
				</xsl:if>
				<xsl:if test="shuyoshohinbunrui='1'">
					<tr>
						<td class="bdr" align="left"><u><xsl:value-of select="shuyoshohin"/></u></td>
						<td class="bdr" align="left"><xsl:value-of select="tani"/></td>
						<td class="bdr" align="right"><xsl:value-of select="suryo"/></td>
						<td class="bdr" align="right"><xsl:value-of select="suryonobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="kagaku"/></td>
						<td class="bdr" align="right"><xsl:value-of select="koseihi"/></td>
						<td class="bdr" align="right"><xsl:value-of select="kagakunobiritsu"/></td>
						<td class="bdr" align="right"><xsl:value-of select="zogenkiyodo"/></td>
					</tr>
				</xsl:if>
				<xsl:if test="shuyoshohinbunrui='2'">
					<tr>
						<td align="left">　<xsl:value-of select="shuyoshohin"/></td>
						<td align="left"><xsl:value-of select="tani"/></td>
						<td align="right"><xsl:value-of select="suryo"/></td>
						<td align="right"><xsl:value-of select="suryonobiritsu"/></td>
						<td align="right"><xsl:value-of select="kagaku"/></td>
						<td align="right"><xsl:value-of select="koseihi"/></td>
						<td align="right"><xsl:value-of select="kagakunobiritsu"/></td>
						<td align="right"><xsl:value-of select="zogenkiyodo"/></td>
					</tr>
				</xsl:if>

			</xsl:for-each>
		</table>
		（Notes） ・ "Percent Change" means "Percent Change from the same term in the preceding year." Figures" with "*" means "Multiplier to the same term in the preceding year."<br/>
		　　　　　 ・ "Contribution degree" means "Percentage of the Yen Change of that category of goods to Grand Total of same term in the preceding year."<br/>
		<br/>
		<br/>
		<div align="center"><xsl:value-of select="page"/></div>
		<br/>
		</div>
</xsl:template>
<xsl:template match="shisu">
		<a><xsl:attribute name="name"><xsl:value-of select="@name"/></xsl:attribute></a>
		<div align="right">
		<div class="pagetop">
		<img src="/toukei/img/common/arrow_up.gif" alt="Top of Page" width="9"/><a href="#top">
		Top of Page
		</a></div></div>
		<hr/>
		<br style="page-break-after:always;"/>
		<div align="right"><xsl:value-of select="taishoym"/></div><br/>
		<h2 class='contents'>
		Indexes of Trade
		</h2>

		<div class="srch">
		<table class="standard">
			<tr>
				<th width="22%" align="center" ></th>
				<th width="13%" align="center">Export of Value Index</th>
				<th width="13%" align="center">Export of Quantum Index</th>
				<th width="13%" align="center">Export of Unit Value Index</th>
				<th width="13%" align="center">Import of Value Index</th>
				<th width="13%" align="center">Import of Quantum Index</th>
				<th width="13%" align="center">Import of Unit Value Index</th>
			</tr>
			<xsl:for-each select="genchi/shisuinfo">
				<tr>
					<td align="center"><xsl:value-of select="chiiki"/></td>
					<td align="right"><xsl:value-of select="exportkingaku"/></td>
					<td align="right"><xsl:value-of select="exportsuryo"/></td>
					<td align="right"><xsl:value-of select="exportkakaku"/></td>
					<td align="right"><xsl:value-of select="importkingaku"/></td>
					<td align="right"><xsl:value-of select="importsuryo"/></td>
					<td align="right"><xsl:value-of select="importkakaku"/></td>
				</tr>
			</xsl:for-each>
		</table>
		（Notes） ・ <xsl:value-of select="chushaku"/>
		 <br/>		　 　　　　・ "ASIA" includes "CHINA".<br/>
		</div>
		<br/>
		<div class="srch">
		<div align="left">Percent Change</div>
		<table class="standard">
			<tr>
				<th width="22%" align="center" ></th>
				<th width="13%" align="center">Export of Value Index</th>
				<th width="13%" align="center">Export of Quantum Index</th>
				<th width="13%" align="center">Export of Unit Value Index</th>
				<th width="13%" align="center">Import of Value Index</th>
				<th width="13%" align="center">Import of Quantum Index</th>
				<th width="13%" align="center">Import of Unit Value Index</th>
			</tr>
			<xsl:for-each select="nobiritsu/shisuinfo">
				<tr>
					<td align="center"><xsl:value-of select="chiiki"/></td>
					<td align="right"><xsl:value-of select="exportkingaku"/></td>
					<td align="right"><xsl:value-of select="exportsuryo"/></td>
					<td align="right"><xsl:value-of select="exportkakaku"/></td>
					<td align="right"><xsl:value-of select="importkingaku"/></td>
					<td align="right"><xsl:value-of select="importsuryo"/></td>
					<td align="right"><xsl:value-of select="importkakaku"/></td>
				</tr>
			</xsl:for-each>
		</table>
		（Notes） ・ "Percent Change" means "Percent Change from the same month in the preceding year.
		<br/>
		<br/>
		<div align="center"><xsl:value-of select="page"/></div>
		<br/>
		</div>
</xsl:template>
<xsl:template match="chiikishisu">
		<a><xsl:attribute name="name"><xsl:value-of select="@name"/></xsl:attribute></a>
		<div align="right">
		<div class="pagetop">
		<img src="/toukei/img/common/arrow_up.gif" alt="Top of Page" width="9"/><a href="#top">
		Top of Page
		</a></div></div>
		<hr/>
		<br style="page-break-after:always;"/>
		<h2 class='contents'>
		Indexes of Trade　<xsl:value-of select="title"/>
		</h2>
		<div class="srch">
		<table class="standard">
			<tr>
				<th width="18%" align="center" ></th>
				<th width="3%" align="center"></th>
				<th width="13%" align="center">Export of Value Index</th>
				<th width="13%" align="center">Export of Quantum Index</th>
				<th width="13%" align="center">Export of Unit Value Index</th>
				<th width="3%" align="center"></th>
				<th width="13%" align="center">Import of Value Index</th>
				<th width="13%" align="center">Import of Quantum Index</th>
				<th width="13%" align="center">Import of Unit Value Index</th>
			</tr>
			<xsl:for-each select="genchi/shisuinfo">
				<tr>
					<td align="center"><xsl:value-of select="taishokikan"/></td>
					<td class="bdr" align="center"><xsl:value-of select="exportp"/></td>
					<td align="right"><xsl:value-of select="exportkingaku"/></td>
					<td align="right"><xsl:value-of select="exportsuryo"/></td>
					<td align="right"><xsl:value-of select="exportkakaku"/></td>
					<td class="bdr" align="center"><xsl:value-of select="importp"/></td>
					<td align="right"><xsl:value-of select="importkingaku"/></td>
					<td align="right"><xsl:value-of select="importsuryo"/></td>
					<td align="right"><xsl:value-of select="importkakaku"/></td>
				</tr>
			</xsl:for-each>
		</table>
		（Notes） ・ <xsl:value-of select="chushaku1"/>
				<xsl:if test="chushaku2='1'">
		　 　　　　・ "(P)" means "Provisional."
				</xsl:if>
				<xsl:if test="chushaku3='3'">
		　 　　　　<br/>		　 　　　　・ "ASIA" includes "CHINA".<br/>
				</xsl:if>

		</div>
		<br/>
		<div class="srch">
		<div align="left">Percent Change</div>
		<table class="standard">
			<tr>
				<th width="18%" align="center"></th>
				<th width="3%" align="center"></th>
				<th width="13%" align="center">Export of Value Index</th>
				<th width="13%" align="center">Export of Quantum Index</th>
				<th width="13%" align="center">Export of Unit Value Index</th>
				<th width="3%" align="center"></th>
				<th width="13%" align="center">Import of Value Index</th>
				<th width="13%" align="center">Import of Quantum Index</th>
				<th width="13%" align="center">Import of Unit Value Index</th>
			</tr>
			<xsl:for-each select="nobiritsu/shisuinfo">
				<tr>
					<td align="center"><xsl:value-of select="taishokikan"/></td>
					<td class="bdr" align="center"><xsl:value-of select="exportp"/></td>
					<td align="right"><xsl:value-of select="exportkingaku"/></td>
					<td align="right"><xsl:value-of select="exportsuryo"/></td>
					<td align="right"><xsl:value-of select="exportkakaku"/></td>
					<td class="bdr" align="center"><xsl:value-of select="importp"/></td>
					<td align="right"><xsl:value-of select="importkingaku"/></td>
					<td align="right"><xsl:value-of select="importsuryo"/></td>
					<td align="right"><xsl:value-of select="importkakaku"/></td>
				</tr>
			</xsl:for-each>
		</table>
		（Notes） ・ "Percent Change" means "Percent Change from the same month in the preceding year." Figures with "*" means "Multiplier to the same month in the preceding year." <br/>
				<xsl:if test="chushaku2='1'">
		　 　　　　・ "(P)" means "Provisional."<br/>
				</xsl:if>
		<br/>
		<br/>
		<div align="center"><xsl:value-of select="page"/></div>
		<br/>
		</div>
</xsl:template>
</xsl:stylesheet>
